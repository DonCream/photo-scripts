#!/usr/bin/env python3
"""
moondream_cull — AI-powered photo culling using Ollama
Prompts for model and album name, sorts files into raw/ jpg/ mp4/,
rates photos 1–5 and appends rating to filename, then moves the
finished album folder to /run/media/doncreamy/diskBackups/Photo Albums/

Requirements:
    pip install rawpy pillow requests tqdm --break-system-packages
    ollama pull moondream

Usage:
    moondream_cull /path/to/photos
    moondream_cull /path/to/photos --dry-run
"""

import argparse
import base64
import io
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import requests
from tqdm import tqdm

try:
    import rawpy
    HAS_RAWPY = True
except ImportError:
    HAS_RAWPY = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ── File type buckets ─────────────────────────────────────────────────────────

RAW_EXTENSIONS   = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf"}
JPEG_EXTENSIONS  = {".jpg", ".jpeg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts", ".webm"}

ALL_EXTENSIONS = RAW_EXTENSIONS | JPEG_EXTENSIONS | VIDEO_EXTENSIONS

FOLDER_MAP = {
    **{ext: "raw" for ext in RAW_EXTENSIONS},
    **{ext: "jpg" for ext in JPEG_EXTENSIONS},
    **{ext: "mp4" for ext in VIDEO_EXTENSIONS},
}

# ── Config ────────────────────────────────────────────────────────────────────

OLLAMA_URL      = "http://localhost:11434/api/generate"
OPENROUTER_URL  = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS = [
    {
        "id":    "anthropic/claude-sonnet-4-6",
        "label": "Claude Sonnet 4.6",
        "desc":  "Best overall — sharpest reasoning, most accurate JSON output, best at judging expression and composition nuance",
        "cost":  "$0.003/1K tokens",
    },
    {
        "id":    "google/gemini-flash-1.5",
        "label": "Gemini 2.0 Flash",
        "desc":  "Fastest vision model, 1M token context, excellent at scene description and detail detection, very low cost",
        "cost":  "$0.0001/1K tokens",
    },
    {
        "id":    "openai/gpt-4o",
        "label": "GPT-4o",
        "desc":  "Strongest at reading emotion and facial expression — good for portrait-heavy shoots, reliable JSON",
        "cost":  "$0.005/1K tokens",
    },
    {
        "id":    "meta-llama/llama-4-maverick",
        "label": "Llama 4 Maverick",
        "desc":  "Open source, 400B params, strong multimodal reasoning, good balance of quality and cost",
        "cost":  "$0.0002/1K tokens",
    },
    {
        "id":    "mistralai/mistral-small-3.1-24b-instruct",
        "label": "Mistral Small 3.1",
        "desc":  "Lightweight vision model, fast inference, good for large batches where cost matters more than precision",
        "cost":  "$0.0001/1K tokens",
    },
]
BACKUP_ROOT  = None  # Set at runtime via --backup-dir flag or interactive prompt
DARKTABLE_DB = Path.home() / ".config" / "darktable" / "library.db"
PREVIEW_SIZE = (1024, 1024)

RATING_PROMPT = """You are a professional photo editor and curator. Analyze this image and rate it on a scale of 1 to 5 based on these three criteria:

1. SHARP FOCUS — Is the primary subject in sharp focus? Are eyes (if present) sharp?
2. COMPOSITION — Does the framing, rule of thirds, leading lines, and background work well?
3. EXPRESSION / EMOTION — Does the subject convey a compelling or natural expression? (If no subject, assess overall mood/impact.)

Scoring guide:
  5 = Exceptional — keeper, publish-ready
  4 = Good — minor flaws, worth keeping
  3 = Average — technically acceptable but not standout
  2 = Below average — significant issues
  1 = Reject — out of focus, badly exposed, or unusable

Respond with ONLY valid JSON, no extra text, in this exact format:
{
  "rating": <integer 1-5>,
  "focus_score": <integer 1-5>,
  "composition_score": <integer 1-5>,
  "expression_score": <integer 1-5>,
  "reason": "<one sentence summary>"
}"""

# ── Interactive prompts ───────────────────────────────────────────────────────

def prompt_model() -> str:
    """Show available Ollama models and let the user pick one."""
    try:
        r    = requests.get("http://localhost:11434/api/tags", timeout=5)
        tags = [m["name"] for m in r.json().get("models", [])]
    except requests.ConnectionError:
        print("Ollama is not running. Start it with:  ollama serve")
        sys.exit(1)

    if not tags:
        print("No models found. Pull one first, e.g.:  ollama pull moondream")
        sys.exit(1)

    print("\nAvailable Ollama models:")
    for i, name in enumerate(tags, 1):
        print(f"  [{i}] {name}")

    while True:
        choice = input("\nEnter model number or name (default: moondream): ").strip()
        if choice == "":
            # Default — pick moondream if available, else first in list
            default = next((t for t in tags if "moondream" in t), tags[0])
            print(f"  Using: {default}")
            return default.split(":")[0]
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(tags):
                selected = tags[idx].split(":")[0]
                print(f"  Using: {selected}")
                return selected
            print(f"  Invalid number, enter 1–{len(tags)}")
        else:
            # User typed a name directly
            match = next((t for t in tags if t.startswith(choice)), None)
            if match:
                selected = match.split(":")[0]
                print(f"  Using: {selected}")
                return selected
            print(f"  Model '{choice}' not found in list, try again")


def prompt_album_name() -> str:
    """Ask for the album/folder name for the backup destination."""
    while True:
        name = input("\nAlbum name for backup (e.g. 'Wedding 2024-06-15'): ").strip()
        if name:
            return name
        print("  Album name cannot be empty.")


def prompt_base_name() -> str:
    """Ask for the base name to use when renaming all files."""
    while True:
        name = input("\nBase name for all files (e.g. 'birthday_party'): ").strip()
        if not name:
            print("  Base name cannot be empty.")
            continue
        # Replace spaces with underscores, strip unsafe characters
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        if safe != name:
            print(f"  Sanitized to: {safe}")
        return safe

def prompt_backend() -> tuple:
    """Ask user to choose Ollama (local) or OpenRouter (cloud). Returns (backend, model)."""
    print("\nAI Backend:")
    print("  [1] Ollama  — local, free, private")
    print("  [2] OpenRouter — cloud, higher quality, costs per image")

    while True:
        choice = input("\nChoose backend [1/2] (default: 1): ").strip()
        if choice in ("", "1"):
            return "ollama", None   # model selected separately via prompt_model
        if choice == "2":
            return "openrouter", prompt_openrouter_model()
        print("  Enter 1 or 2")


def prompt_openrouter_model() -> str:
    """Show top vision models with descriptions and let user pick one."""
    print("\nTop vision models for photo culling:\n")
    for i, m in enumerate(OPENROUTER_MODELS, 1):
        print(f"  [{i}] {m['label']:<28} {m['cost']}")
        print(f"       {m['desc']}")
        print()
    while True:
        choice = input("Model number (default: 1): ").strip()
        if choice == "":
            return OPENROUTER_MODELS[0]["id"]
        if choice.isdigit() and 1 <= int(choice) <= len(OPENROUTER_MODELS):
            return OPENROUTER_MODELS[int(choice) - 1]["id"]
        print(f"  Enter 1–{len(OPENROUTER_MODELS)}")


def prompt_backup_dir() -> Path:
    """Ask the user where to archive the finished album folder."""
    print("\nWhere should finished albums be saved?")
    print("  (Drag and drop the folder into the terminal, or type the path)")
    while True:
        raw = input("Backup destination: ").strip().strip("'\"")
        if not raw:
            print("  Path cannot be empty.")
            continue
        p = Path(raw).expanduser().resolve()
        if p.exists() and p.is_dir():
            print(f"  Using: {p}")
            return p
        # Path doesn't exist yet — offer to create it
        ans = input(f"  '{p}' doesn't exist. Create it? [Y/n]: ").strip().lower()
        if ans in ("", "y"):
            try:
                p.mkdir(parents=True, exist_ok=True)
                print(f"  Created: {p}")
                return p
            except Exception as e:
                print(f"  ERROR creating directory: {e}")
        else:
            print("  Enter a different path.")


def load_openrouter_key() -> str:
    """Load OpenRouter API key from env or Fabric config."""
    key = __import__("os").environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    fabric_env = Path.home() / ".config" / "fabric" / ".env"
    if fabric_env.exists():
        for line in fabric_env.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
                if key:
                    return key
    return ""



def check_dependencies():
    missing = []
    if not HAS_PIL:
        missing.append("Pillow  →  pip install pillow --break-system-packages")
    if not HAS_RAWPY:
        missing.append("rawpy   →  pip install rawpy --break-system-packages")
    if missing:
        print("Missing dependencies:")
        for m in missing:
            print(f"  {m}")
        sys.exit(1)


def check_exiftool() -> bool:
    """Returns True if exiftool is available on PATH."""
    import shutil as sh
    found = sh.which("exiftool") is not None
    if not found:
        print("  Warning: exiftool not found — metadata will not be written.")
        print("  Install with:  sudo pacman -S perl-image-exiftool")
    return found


def check_backup_drive(backup_root: Path):
    """Warn early if the backup drive isn't mounted."""
    if not backup_root.exists():
        print(f"\nWarning: backup destination not found: {backup_root}")
        print("  Make sure the drive is mounted before the final move step.")
        ans = input("  Continue anyway? [y/N]: ").strip().lower()
        if ans != "y":
            sys.exit(0)

# ── Image helpers ─────────────────────────────────────────────────────────────

def image_to_base64(img: "Image.Image") -> str:
    img.thumbnail(PREVIEW_SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def load_image(path: Path) -> "Image.Image":
    """
    Load any supported image into a PIL RGB image.
    For RAW files, tries rawpy first, then falls back to
    dcraw/exiftool extraction, then a pure-PIL attempt.
    Raises RuntimeError if all methods fail.
    """
    import subprocess as sp
    ext = path.suffix.lower()

    if ext in RAW_EXTENSIONS and HAS_RAWPY:
        # Primary: rawpy
        try:
            with rawpy.imread(str(path)) as raw:
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    half_size=True,
                    no_auto_bright=False,
                    output_bps=8,
                )
            img = Image.fromarray(rgb)
            if img.width > 0 and img.height > 0:
                return img.convert("RGB")
        except Exception as e:
            tqdm.write(f"    rawpy failed for {path.name}: {e} — trying fallback")

        # Fallback 1: extract embedded JPEG preview with exiftool
        try:
            import tempfile, os
            with tempfile.TemporaryDirectory() as tmpdir:
                out = sp.run(
                    ["exiftool", "-b", "-PreviewImage", str(path)],
                    capture_output=True, timeout=30
                )
                if out.returncode == 0 and len(out.stdout) > 1000:
                    img = Image.open(io.BytesIO(out.stdout)).convert("RGB")
                    tqdm.write(f"    used exiftool preview for {path.name}")
                    return img
        except Exception:
            pass

        # Fallback 2: dcraw
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                out = sp.run(
                    ["dcraw", "-c", "-w", "-h", str(path)],
                    capture_output=True, timeout=60
                )
                if out.returncode == 0 and out.stdout:
                    img = Image.open(io.BytesIO(out.stdout)).convert("RGB")
                    tqdm.write(f"    used dcraw for {path.name}")
                    return img
        except Exception:
            pass

        raise RuntimeError(f"Could not decode RAW file: {path.name}")

    # JPEG / standard formats
    return Image.open(path).convert("RGB")


def extract_json(raw_text: str) -> dict:
    """
    Robustly extract the first valid JSON object from a model response.
    Handles extra text, single quotes, trailing commas, and moondream quirks.
    """
    import re

    text = re.sub(r"```(?:json)?", "", raw_text).strip()

    pos = 0
    while True:
        start = text.find("{", pos)
        if start == -1:
            break
        depth = 0
        found_end = False
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    for attempt in [
                        candidate,
                        candidate.replace("'", '"'),
                        re.sub(r",\s*([}\]])", r"\1", candidate),
                        re.sub(r",\s*([}\]])", r"\1", candidate.replace("'", '"')),
                    ]:
                        try:
                            return json.loads(attempt)
                        except json.JSONDecodeError:
                            pass
                    pos = start + 1
                    found_end = True
                    break
        if not found_end:
            break

    raise ValueError(f"RAW RESPONSE ({len(raw_text)} chars):\n{repr(raw_text[:800])}")


def query_ollama(b64_image: str, model: str) -> dict:
    payload = {
        "model": model,
        "prompt": RATING_PROMPT,
        "images": [b64_image],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    r = requests.post(OLLAMA_URL, json=payload, timeout=120)
    r.raise_for_status()
    raw_text = r.json().get("response", "")
    return extract_json(raw_text)


def query_openrouter(b64_image: str, model: str, api_key: str) -> dict:
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": RATING_PROMPT},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{b64_image}"
                }},
            ]
        }],
        "temperature": 0.1,
    }
    r = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "https://webjellystudios.com",
            "X-Title":       "WebJelly Cull",
        },
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    raw_text = r.json()["choices"][0]["message"]["content"]
    return extract_json(raw_text)

def write_metadata(dest: Path, rating: int, reason: str,
                   focus: int, comp: int, expr: int, has_exiftool: bool):
    """Write star rating and AI analysis into the copied file's EXIF/XMP."""
    if not has_exiftool or not dest.exists():
        return
    import subprocess
    # XMP:Rating        — standard star rating (0-5), read by darktable, digiKam, Lightroom
    # IPTC:Caption-Abstract — human-readable reason string
    # XMP:Description   — same reason, in XMP namespace for broader compatibility
    # XMP:Subject       — searchable tags for focus/comp/expression scores
    cmd = [
        "exiftool",
        "-overwrite_original",
        f"-XMP:Rating={rating}",
        f"-IPTC:Caption-Abstract={reason}",
        f"-XMP:Description={reason}",
        f"-XMP:Subject=focus_{focus}star",
        f"-XMP:Subject+=composition_{comp}star",
        f"-XMP:Subject+=expression_{expr}star",
        f"-XMP:Subject+={rating}star",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        tqdm.write(f"    exiftool warning: {e.stderr.decode().strip()}")

def build_filename(base_name: str, number: int, total: int, rating: int, suffix: str) -> str:
    """
    birthday_party, 3, 47, 4, .jpg  →  birthday_party_003.jpg
    Zero-pads the number to match the width of total, e.g. 001 for up to 999 files.
    Rating is stored in EXIF/XMP only, not in the filename.
    """
    pad = len(str(total))
    num = str(number).zfill(pad)
    return f"{base_name}_{num}{suffix}"


def build_video_filename(base_name: str, number: int, total: int, suffix: str) -> str:
    """birthday_party, 5, 47, .mp4  →  birthday_party_005.mp4"""
    pad = len(str(total))
    num = str(number).zfill(pad)
    return f"{base_name}_{num}{suffix}"


def unique_dest(folder: Path, filename: str) -> Path:
    dest = folder / filename
    if not dest.exists():
        return dest
    stem    = Path(filename).stem
    suffix  = Path(filename).suffix
    counter = 1
    while dest.exists():
        dest = folder / f"{stem}_{counter}{suffix}"
        counter += 1
    return dest

# ── Core ──────────────────────────────────────────────────────────────────────

def ensure_output_dirs(base: Path) -> dict:
    dirs = {}
    for name in ("raw", "jpg", "mp4"):
        d = base / name
        d.mkdir(parents=True, exist_ok=True)
        dirs[name] = d
    return dirs


def process_directory(photo_dir: Path, model: str, album_name: str, base_name: str,
                      dry_run: bool, has_exiftool: bool,
                      backend: str = "ollama", api_key: str = "",
                      backup_root: Path = None):
    files = sorted(
        f for f in photo_dir.iterdir()
        if f.is_file() and f.suffix.lower() in ALL_EXTENSIONS
    )

    if not files:
        print(f"\nNo supported files found in {photo_dir}")
        sys.exit(0)

    total = len(files)
    print(f"\nFound {total} file(s) in {photo_dir}")
    print(f"Backend: {backend}  |  Model: {model}  |  Album: {album_name}  |  Base name: {base_name}  |  Dry run: {dry_run}\n")

    # Create raw/ jpg/ mp4/ directly inside the named album folder on the backup drive
    album_dir = backup_root / album_name
    if not dry_run:
        album_dir.mkdir(parents=True, exist_ok=True)
    out_dirs = ensure_output_dirs(album_dir) if not dry_run else {
        "raw": album_dir / "raw",
        "jpg": album_dir / "jpg",
        "mp4": album_dir / "mp4",
    }

    results  = []

    # Separate counters per bucket so each subfolder starts at 1
    counters = {"raw": 0, "jpg": 0, "mp4": 0}

    # Pre-count per bucket for zero-padding width
    bucket_totals = {"raw": 0, "jpg": 0, "mp4": 0}
    for f in files:
        b = FOLDER_MAP.get(f.suffix.lower(), "mp4")
        bucket_totals[b] += 1

    # ── Progress bar wraps the file list ─────────────────────────────────────
    bar = tqdm(
        files,
        desc="Culling",
        unit="file",
        ncols=80,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
    )

    for fpath in bar:
        ext      = fpath.suffix.lower()
        bucket   = FOLDER_MAP.get(ext, "mp4")
        is_video = ext in VIDEO_EXTENSIONS
        dest_dir = out_dirs[bucket]

        counters[bucket] += 1
        number            = counters[bucket]
        btotal            = bucket_totals[bucket]

        bar.set_postfix_str(fpath.name[:30])

        # ── Videos: rename and move, no rating ──────────────────────────────
        if is_video:
            new_name = build_video_filename(base_name, number, btotal, ext)
            dest     = unique_dest(dest_dir, new_name)
            if not dry_run:
                shutil.copy2(str(fpath), str(dest))
            results.append({
                "file": fpath.name,
                "new_name": new_name,
                "type": "video",
                "dest": str(dest),
            })
            tqdm.write(f"  video  {fpath.name}  →  mp4/{new_name}")
            continue

        # ── Photos: rate then rename and move ────────────────────────────────
        try:
            img    = load_image(fpath)
            b64    = image_to_base64(img)
            if backend == "openrouter":
                result = query_openrouter(b64, model, api_key)
            else:
                result = query_ollama(b64, model)

            rating = max(1, min(5, int(result.get("rating", 3))))
            reason = result.get("reason", "")
            focus  = result.get("focus_score", "?")
            comp   = result.get("composition_score", "?")
            expr   = result.get("expression_score", "?")

            new_name = build_filename(base_name, number, btotal, rating, ext)
            dest     = unique_dest(dest_dir, new_name)

            tqdm.write(
                f"  ★{rating}  {fpath.name}  →  {bucket}/{new_name}"
                f"  [focus={focus} comp={comp} expr={expr}]"
                f"\n       {reason}"
            )

            if not dry_run:
                shutil.copy2(str(fpath), str(dest))
                write_metadata(dest, rating, reason, focus, comp, expr, has_exiftool)

            results.append({
                "file":              fpath.name,
                "new_name":          new_name,
                "type":              bucket,
                "rating":            rating,
                "focus_score":       focus,
                "composition_score": comp,
                "expression_score":  expr,
                "reason":            reason,
            })

        except Exception as e:
            tqdm.write(f"  ERROR  {fpath.name}: {e}")
            results.append({"file": fpath.name, "error": str(e)})

    bar.close()

    # ── Write cull_results.json into the album folder ────────────────────────
    summary_path = (backup_root / album_name / "cull_results.json") if not dry_run else (photo_dir / "cull_results.json")
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSummary written → {summary_path}")
    print_summary(results)
    print(f"\nAlbum saved to → {backup_root / album_name}")

    # ── Darktable: import, sync ratings, launch ───────────────────────────────
    run_darktable_workflow(results, backup_root / album_name, dry_run)

# ── Darktable integration ─────────────────────────────────────────────────────

def darktable_import_raw_folder(raw_dir: Path):
    """
    Import the raw/ folder into darktable's library by calling darktable-cli.
    darktable-cli with no output file and --import just registers the folder.
    """
    print(f"\n── Importing into darktable ──────────────")
    print(f"  Folder: {raw_dir}")

    if not raw_dir.exists():
        print("  ERROR: raw/ folder not found, skipping import")
        return False

    import shutil as sh
    if not sh.which("darktable-cli"):
        print("  ERROR: darktable-cli not found — is darktable installed?")
        return False

    try:
        result = subprocess.run(
            ["darktable-cli", "--import", str(raw_dir)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            print("  Import complete.")
            return True
        else:
            # darktable-cli returns non-zero even on success in some versions
            # check stderr for actual errors
            if "error" in result.stderr.lower():
                print(f"  Warning: {result.stderr.strip()}")
            else:
                print("  Import complete.")
            return True
    except subprocess.TimeoutExpired:
        print("  ERROR: darktable-cli timed out during import")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def sync_ratings_to_darktable(results: list, raw_dir: Path):
    """
    Write AI star ratings from cull results into darktable's SQLite library.
    Only processes RAW file entries.
    """
    print(f"\n── Syncing ratings to darktable ──────────")

    if not DARKTABLE_DB.exists():
        print(f"  ERROR: darktable library not found at {DARKTABLE_DB}")
        print("  Open darktable at least once to initialize the library.")
        return

    raw_results = [r for r in results if r.get("type") == "raw" and "rating" in r]
    if not raw_results:
        print("  No rated RAW files to sync.")
        return

    # Backup DB before writing
    from datetime import datetime
    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DARKTABLE_DB.with_name(f"library_backup_{ts}.db")
    shutil.copy2(str(DARKTABLE_DB), str(backup))
    print(f"  DB backup → {backup.name}")

    conn   = sqlite3.connect(str(DARKTABLE_DB))
    cursor = conn.cursor()
    synced = 0
    missed = 0

    for entry in raw_results:
        new_name = entry.get("new_name") or entry.get("file")
        rating   = entry["rating"]

        # Search by filename in darktable's images table
        cursor.execute("""
            SELECT i.id, f.folder FROM images i
            JOIN film_rolls f ON i.film_id = f.id
            WHERE i.filename = ?
        """, (new_name,))
        rows = cursor.fetchall()

        if not rows:
            missed += 1
            continue

        # Prefer the row whose folder matches our raw_dir
        img_id = None
        raw_str = str(raw_dir).rstrip("/")
        for row_id, folder in rows:
            if folder.rstrip("/") == raw_str:
                img_id = row_id
                break
        if img_id is None:
            img_id = rows[0][0]

        # flags field stores rating in lower 3 bits
        cursor.execute(
            "UPDATE images SET flags = (flags & ~7) | ? WHERE id = ?",
            (rating, img_id)
        )
        synced += 1

    conn.commit()
    conn.close()

    print(f"  Synced  : {synced} file(s)")
    if missed:
        print(f"  Missed  : {missed} (not yet in darktable library)")


def launch_darktable(raw_dir: Path):
    """
    Launch darktable in lighttable view, focused on the raw/ folder.
    Runs in the background so the terminal returns immediately.
    """
    print(f"\n── Launching darktable ───────────────────")

    import shutil as sh
    if not sh.which("darktable"):
        print("  ERROR: darktable not found on PATH")
        return

    # --library keeps darktable using its default library
    # The folder path at the end tells darktable to open that film roll
    # in lighttable view on startup
    cmd = [
        "darktable",
        "--library", str(DARKTABLE_DB),
        str(raw_dir),
    ]
    try:
        subprocess.Popen(cmd)
        print(f"  Darktable opening → {raw_dir}")
        print("  Re-rate or edit files, then export when ready.")
    except Exception as e:
        print(f"  ERROR launching darktable: {e}")


def run_darktable_workflow(results: list, album_dir: Path, dry_run: bool):
    """Full darktable post-cull workflow: import → sync ratings → launch."""
    if dry_run:
        print("\n── Darktable workflow (dry-run, skipped) ─")
        return

    raw_dir = album_dir / "raw"

    # 1. Import raw folder into darktable library
    imported = darktable_import_raw_folder(raw_dir)

    # 2. Sync AI ratings into the DB
    if imported:
        sync_ratings_to_darktable(results, raw_dir)

    # 3. Ask before launching so user can choose to skip
    ans = input("\nOpen darktable now to review and re-rate? [Y/n]: ").strip().lower()
    if ans in ("", "y"):
        launch_darktable(raw_dir)


def print_summary(results: list):
    from collections import Counter
    photo_results = [r for r in results if r.get("type") in ("raw", "jpg")]
    video_results = [r for r in results if r.get("type") == "video"]
    errors        = [r for r in results if "error" in r]
    counts        = Counter(r.get("rating") for r in photo_results if "rating" in r)

    print("\n── Rating distribution ───────────────────")
    print(f"  Photos rated : {len(photo_results)}")
    print(f"  Videos moved : {len(video_results)}")
    if errors:
        print(f"  Errors       : {len(errors)}")
    print()
    for star in range(5, 0, -1):
        bar = "█" * counts.get(star, 0)
        print(f"  ★{star}  {bar}  ({counts.get(star, 0)})")

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sort, rate, and archive photos with AI"
    )
    parser.add_argument("directory", nargs="?", default="",
                        help="Folder containing files to process (drag and drop or type path)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only, don't move or copy files")
    parser.add_argument("--backup-dir", default="",
                        help="Destination root for archived albums (overrides interactive prompt)")
    args = parser.parse_args()

    if args.directory:
        photo_dir = Path(args.directory).expanduser().resolve()
    else:
        print("\nDrag and drop your photo folder into the terminal and press Enter:")
        folder = input("Folder: ").strip().strip("'\"")
        if not folder:
            print("No folder entered.")
            sys.exit(0)
        photo_dir = Path(folder).expanduser().resolve()

    if not photo_dir.is_dir():
        print(f"Not a directory: {photo_dir}")
        sys.exit(1)

    check_dependencies()

    # Resolve backup destination
    if args.backup_dir:
        backup_root = Path(args.backup_dir).expanduser().resolve()
        backup_root.mkdir(parents=True, exist_ok=True)
    else:
        backup_root = prompt_backup_dir()

    check_backup_drive(backup_root)
    has_exiftool = check_exiftool()

    # Interactive prompts
    backend, or_model = prompt_backend()

    if backend == "ollama":
        model   = prompt_model()
        api_key = ""
    else:
        model   = or_model
        api_key = load_openrouter_key()
        if not api_key:
            print("ERROR: OpenRouter API key not found.")
            print("  Add OPENROUTER_API_KEY to ~/.config/fabric/.env")
            sys.exit(1)
        print(f"  Using OpenRouter key: {api_key[:8]}...")

    album_name = prompt_album_name()
    base_name  = prompt_base_name()

    process_directory(photo_dir, model, album_name, base_name,
                      args.dry_run, has_exiftool, backend, api_key,
                      backup_root=backup_root)


if __name__ == "__main__":
    main()
