#!/usr/bin/env python3
"""
dt_import — Walk a chosen /mnt/ drive, find every folder containing RAW files,
and register each as a film roll in darktable's library.

No assumed directory structure — any folder with RAW files gets imported.

IMPORTANT: darktable must be CLOSED before running this script.

Requirements:
    Python 3.6+  (sqlite3 is standard library)

Usage:
    dt_import
    dt_import --dry-run
    dt_import --drive /mnt/DiskBackups          # skip the picker
"""

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

DARKTABLE_DB   = Path.home() / ".config" / "darktable" / "library.db"
MNT_ROOT       = Path("/mnt")
RAW_EXTENSIONS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf"}

# ── Drive picker ──────────────────────────────────────────────────────────────

def list_mnt_drives() -> list[Path]:
    if not MNT_ROOT.exists():
        return []
    drives = []
    for child in sorted(MNT_ROOT.iterdir()):
        if not child.is_dir():
            continue
        try:
            next(child.iterdir())
            drives.append(child)
        except (StopIteration, PermissionError):
            pass
    return drives


def pick_drive() -> Path:
    drives = list_mnt_drives()
    if not drives:
        print("ERROR: No mounted drives found under /mnt/")
        sys.exit(1)

    print("Available drives under /mnt/:\n")
    for i, d in enumerate(drives, 1):
        print(f"  [{i}]  {d}")
    print()

    while True:
        try:
            raw = input(f"Select drive [1-{len(drives)}]: ").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(drives):
                chosen = drives[idx]
                print(f"  -> {chosen}\n")
                return chosen
            print(f"  Please enter a number between 1 and {len(drives)}.")
        except (ValueError, EOFError):
            print("  Invalid input.")

# ── Scan ─────────────────────────────────────────────────────────────────────

def find_raw_folders(drive_root: Path) -> dict[Path, list[Path]]:
    """
    Walk the entire drive. Return {folder: [raw_file, ...]} for every
    directory that contains at least one RAW file.
    Skips folders it can't read rather than crashing.
    """
    result: dict[Path, list[Path]] = {}

    for dirpath, dirnames, filenames in os.walk(drive_root, onerror=_walk_error):
        raw_files = sorted(
            Path(dirpath) / f
            for f in filenames
            if Path(f).suffix.lower() in RAW_EXTENSIONS
        )
        if raw_files:
            result[Path(dirpath)] = raw_files

    return result


def _walk_error(err: OSError):
    print(f"  [warn] Skipping unreadable dir: {err.filename}  ({err.strerror})")

# ── Database helpers ──────────────────────────────────────────────────────────

def backup_db():
    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DARKTABLE_DB.with_name(f"library_backup_{ts}.db")
    shutil.copy2(str(DARKTABLE_DB), str(backup))
    print(f"  DB backup -> {backup.name}\n")


def get_or_create_film_roll(cursor: sqlite3.Cursor, folder: Path) -> tuple[int, bool]:
    folder_str = str(folder).rstrip("/")
    cursor.execute("SELECT id FROM film_rolls WHERE folder = ?", (folder_str,))
    row = cursor.fetchone()
    if row:
        return row[0], False
    now = int(datetime.now().timestamp())
    cursor.execute(
        "INSERT INTO film_rolls (folder, access_timestamp) VALUES (?, ?)",
        (folder_str, now)
    )
    return cursor.lastrowid, True


def image_already_registered(cursor: sqlite3.Cursor, film_id: int, filename: str) -> bool:
    cursor.execute(
        "SELECT id FROM images WHERE film_id = ? AND filename = ?",
        (film_id, filename)
    )
    return cursor.fetchone() is not None


def register_image(cursor: sqlite3.Cursor, film_id: int, fpath: Path, rating: int):
    now = int(datetime.now().timestamp())
    cursor.execute("""
        INSERT INTO images (
            film_id, filename, flags,
            import_timestamp, change_timestamp,
            width, height, datetime_taken
        ) VALUES (?, ?, ?, ?, ?, 0, 0, '')
    """, (film_id, fpath.name, rating, now, now))


def parse_rating_from_filename(filename: str) -> int:
    match = re.search(r"_(\d)star\.", filename, re.IGNORECASE)
    if match:
        return max(1, min(5, int(match.group(1))))
    return 0

# ── Checks ────────────────────────────────────────────────────────────────────

def check_db():
    if not DARKTABLE_DB.exists():
        print(f"ERROR: darktable library not found at {DARKTABLE_DB}")
        print("  Open darktable once to initialize it, close it, then re-run.")
        sys.exit(1)


def check_darktable_closed():
    result = subprocess.run(["pgrep", "-x", "darktable"], capture_output=True)
    if result.returncode == 0:
        print("ERROR: darktable is currently running.")
        print("  Close darktable first, then re-run this script.")
        sys.exit(1)

# ── Core ──────────────────────────────────────────────────────────────────────

def run(drive_root: Path, dry_run: bool):
    print(f"Scanning: {drive_root}  (this may take a moment...)\n")

    folders = find_raw_folders(drive_root)

    if not folders:
        print("No RAW files found on this drive.")
        sys.exit(0)

    total_files = sum(len(v) for v in folders.values())
    print(f"Found {total_files} RAW file(s) in {len(folders)} folder(s):\n")
    for folder, files in sorted(folders.items()):
        print(f"  {folder}  ({len(files)} file(s))")

    if dry_run:
        print("\n[dry-run] Nothing written. Run without --dry-run to register in darktable.")
        return

    print()
    backup_db()

    conn   = sqlite3.connect(str(DARKTABLE_DB))
    cursor = conn.cursor()

    rolls_added    = 0
    rolls_skipped  = 0
    images_added   = 0
    images_skipped = 0

    print("── Registering in darktable ──────────────\n")

    for folder, files in sorted(folders.items()):
        film_id, is_new = get_or_create_film_roll(cursor, folder)

        if is_new:
            print(f"  + {folder}  (new film roll, id={film_id})")
            rolls_added += 1
        else:
            print(f"  = {folder}  (already registered, id={film_id})")
            rolls_skipped += 1

        for fpath in files:
            if image_already_registered(cursor, film_id, fpath.name):
                images_skipped += 1
                continue
            rating = parse_rating_from_filename(fpath.name)
            register_image(cursor, film_id, fpath, rating)
            images_added += 1

    conn.commit()
    conn.close()

    print(f"\n── Summary ───────────────────────────────")
    print(f"  Film rolls added   : {rolls_added}")
    print(f"  Film rolls skipped : {rolls_skipped}  (already in library)")
    print(f"  Images registered  : {images_added}")
    print(f"  Images skipped     : {images_skipped}  (already in library)")
    print(f"\nOpen darktable — your folders will appear in Collections -> Film roll.")

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Register all folders containing RAW files into darktable Collections"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be registered without writing anything"
    )
    parser.add_argument(
        "--drive", metavar="PATH",
        help="Skip the picker and use this drive root directly (e.g. /mnt/DiskBackups)"
    )
    args = parser.parse_args()

    check_db()
    check_darktable_closed()

    if args.drive:
        drive_root = Path(args.drive)
        if not drive_root.exists():
            print(f"ERROR: --drive path does not exist: {drive_root}")
            sys.exit(1)
    else:
        drive_root = pick_drive()

    run(drive_root, args.dry_run)


if __name__ == "__main__":
    main()
