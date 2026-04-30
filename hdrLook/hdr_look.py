#!/usr/bin/env python3
"""
hdr_look — Give single-exposure RAW files an HDR look using rawpy + enfuse.
Renders each RAW at 5 virtual exposures, blends with enfuse, saves to social/HDR/.

Requirements:
    pip install rawpy pillow numpy --break-system-packages
    sudo pacman -S enblend-enfuse

Usage:
    hdr_look                        # prompts for folder
    hdr_look /path/to/raw/folder
    hdr_look /path/to/folder --strength strong
    hdr_look /path/to/folder --strength subtle
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import rawpy
    import numpy as np
    from PIL import Image
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

# ── Config ────────────────────────────────────────────────────────────────────

RAW_EXTENSIONS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf"}

# Virtual exposure stops to render from each RAW
# Each value is a multiplier applied to the brightness
EXPOSURE_PRESETS = {
    "subtle": [-1.5, -0.75, 0, +0.75, +1.5],
    "natural": [-2.0, -1.0,  0, +1.0,  +2.0],
    "strong":  [-2.5, -1.25, 0, +1.25, +2.5],
}
DEFAULT_STRENGTH = "natural"

# enfuse blending options
# --exposure-weight    — how much to weight by exposure level
# --saturation-weight  — how much to weight by color saturation
# --contrast-weight    — how much to weight by local contrast
ENFUSE_ARGS = {
    "subtle":  ["--exposure-weight=1.0", "--saturation-weight=0.2", "--contrast-weight=0.0"],
    "natural": ["--exposure-weight=1.0", "--saturation-weight=0.3", "--contrast-weight=0.1"],
    "strong":  ["--exposure-weight=1.0", "--saturation-weight=0.5", "--contrast-weight=0.3"],
}

# ── Checks ────────────────────────────────────────────────────────────────────

def check_dependencies():
    if not HAS_DEPS:
        print("Missing Python deps:")
        print("  pip install rawpy pillow numpy --break-system-packages")
        sys.exit(1)
    if not shutil.which("enfuse"):
        print("Missing: enfuse")
        print("  sudo pacman -S enblend-enfuse")
        sys.exit(1)

# ── Folder input ──────────────────────────────────────────────────────────────

def pick_folder() -> Path:
    print("\nDrag and drop your RAW folder into the terminal and press Enter:")
    folder = input("Folder: ").strip().strip("'\"")
    if not folder:
        print("No folder entered.")
        sys.exit(0)
    return Path(folder).expanduser().resolve()

# ── RAW rendering ─────────────────────────────────────────────────────────────

def ev_multiplier(stops: float) -> float:
    """Convert EV stops to a linear brightness multiplier."""
    return 2.0 ** stops


def render_exposure(raw_path: Path, stops: float, out_path: Path):
    """
    Render a RAW file at a virtual exposure offset (in EV stops).
    Positive stops = brighter (pull up shadows).
    Negative stops = darker (pull down highlights).
    """
    with rawpy.imread(str(raw_path)) as raw:
        # Get base render at neutral exposure
        rgb = raw.postprocess(
            use_camera_wb   = True,
            half_size       = False,
            no_auto_bright  = True,
            output_bps      = 16,
            exp_shift       = ev_multiplier(stops),
            exp_preserve_highlights = 0.9 if stops < 0 else 0.0,
        )

    img = Image.fromarray(rgb)

    # Save as 16-bit TIFF for enfuse
    img.save(str(out_path), format="TIFF")


# ── enfuse blending ───────────────────────────────────────────────────────────

def blend_exposures(exposure_paths: list, out_path: Path, strength: str):
    """Run enfuse to blend the virtual exposures into one HDR-look image."""
    cmd = [
        "enfuse",
        "--output", str(out_path),
        "--compression=92",
        "--hard-mask",
    ] + ENFUSE_ARGS[strength] + [str(p) for p in exposure_paths]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"enfuse failed: {result.stderr.strip()}")

# ── Core ──────────────────────────────────────────────────────────────────────

def process_raw(raw_path: Path, out_dir: Path,
                strength: str, tmp_dir: Path):
    """
    Full pipeline for one RAW file:
    1. Render virtual exposures
    2. Blend with enfuse
    3. Save TIFF to out_dir
    """
    stops    = EXPOSURE_PRESETS[strength]
    renders  = []

    for i, ev in enumerate(stops):
        label    = f"{'+' if ev >= 0 else ''}{ev:.2f}ev"
        tmp_tiff = tmp_dir / f"{raw_path.stem}_{i:02d}_{label}.tif"
        render_exposure(raw_path, ev, tmp_tiff)
        renders.append(tmp_tiff)

    out_path = out_dir / f"{raw_path.stem}_hdr.jpg"
    blend_exposures(renders, out_path, strength)

    # Clean up temp renders
    for r in renders:
        r.unlink(missing_ok=True)

    return out_path


def process_folder(folder: Path, strength: str):
    files = sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in RAW_EXTENSIONS
    )

    if not files:
        print(f"No RAW files found in {folder}")
        sys.exit(0)

    # Output to social/HDR/ alongside source folder
    out_dir = folder.parent / "social" / "HDR"
    out_dir.mkdir(parents=True, exist_ok=True)

    stops = EXPOSURE_PRESETS[strength]
    print(f"\nFound    : {len(files)} RAW file(s)")
    print(f"Strength : {strength}  ({len(stops)} exposures: "
          f"{', '.join(f'{s:+.1f}EV' for s in stops)})")
    print(f"Output   : {out_dir}\n")

    done = errors = 0

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        for i, fpath in enumerate(files, 1):
            print(f"  [{i}/{len(files)}] {fpath.name}")
            print(f"    Rendering {len(stops)} exposures ...", end="", flush=True)
            try:
                out_path = process_raw(fpath, out_dir, strength, tmp_dir)
                size_mb  = out_path.stat().st_size / 1024 / 1024
                print(f" done")
                print(f"    Saved → {out_path.name}  ({size_mb:.1f} MB)")
                done += 1
            except Exception as e:
                print(f" ERROR: {e}")
                errors += 1

    print(f"\nDone. {done} processed, {errors} errors.")
    print(f"Output: {out_dir}")
    print(f"\nTip: run the watermark script on social/HDR/ to add your logo.")

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Give single-exposure RAWs an HDR look using rawpy + enfuse"
    )
    parser.add_argument("folder",     nargs="?", default="",
                        help="Folder containing RAW files")
    parser.add_argument("--strength", default=DEFAULT_STRENGTH,
                        choices=list(EXPOSURE_PRESETS.keys()),
                        help=f"HDR intensity: subtle / natural / strong "
                             f"(default: {DEFAULT_STRENGTH})")
    args = parser.parse_args()

    check_dependencies()

    folder = Path(args.folder).expanduser().resolve() if args.folder \
             else pick_folder()

    if not folder.is_dir():
        print(f"Not a directory: {folder}")
        sys.exit(1)

    process_folder(folder, args.strength)


if __name__ == "__main__":
    main()
