#!/usr/bin/env python3
"""
dt_apply_style — Apply a saved darktable style to all 4 and 5 star images
in a given film roll folder. Writes directly to darktable's SQLite database.

IMPORTANT: darktable must be CLOSED before running this script directly.
When called from the darktable Lua button, darktable handles the reload.

Usage:
    dt_apply_style /path/to/raw/folder "My Style Name"
    dt_apply_style /path/to/raw/folder "My Style Name" --min-rating 3
    dt_apply_style /path/to/raw/folder --list-styles
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

DARKTABLE_DB      = Path.home() / ".config" / "darktable" / "library.db"
DARKTABLE_DATA_DB = Path.home() / ".config" / "darktable" / "data.db"

# ── DB helpers ────────────────────────────────────────────────────────────────

def connect_library() -> sqlite3.Connection:
    if not DARKTABLE_DB.exists():
        print(f"ERROR: darktable library.db not found at {DARKTABLE_DB}")
        sys.exit(1)
    return sqlite3.connect(str(DARKTABLE_DB))


def connect_data() -> sqlite3.Connection:
    if not DARKTABLE_DATA_DB.exists():
        print(f"ERROR: darktable data.db not found at {DARKTABLE_DATA_DB}")
        sys.exit(1)
    return sqlite3.connect(str(DARKTABLE_DATA_DB))


def backup_db():
    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DARKTABLE_DB.with_name(f"library_backup_{ts}.db")
    shutil.copy2(str(DARKTABLE_DB), str(backup))
    print(f"  DB backup → {backup.name}")


def list_styles(conn: sqlite3.Connection):
    """Print all saved styles in data.db."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, description FROM styles ORDER BY name")
    rows = cursor.fetchall()
    if not rows:
        print("No styles found. Create and save a style in darktable first.")
        return
    print(f"\n{'ID':<6} {'Name':<40} Description")
    print("─" * 80)
    for sid, name, desc in rows:
        print(f"{sid:<6} {name:<40} {desc or ''}")


def get_style(cursor: sqlite3.Cursor, style_name: str) -> int:
    """Return style id for the given name, or exit if not found."""
    cursor.execute(
        "SELECT id FROM styles WHERE name = ?",
        (style_name,)
    )
    row = cursor.fetchone()
    if not row:
        # Try case-insensitive partial match
        cursor.execute(
            "SELECT id, name FROM styles WHERE name LIKE ?",
            (f"%{style_name}%",)
        )
        matches = cursor.fetchall()
        if not matches:
            print(f"ERROR: Style '{style_name}' not found in darktable library.")
            print("  Run with --list-styles to see available styles.")
            sys.exit(1)
        if len(matches) == 1:
            print(f"  Matched style: '{matches[0][1]}'")
            return matches[0][0]
        print(f"ERROR: Multiple styles match '{style_name}':")
        for sid, sname in matches:
            print(f"  [{sid}] {sname}")
        print("  Use the exact name.")
        sys.exit(1)
    return row[0]


def get_style_items(cursor: sqlite3.Cursor, style_id: int) -> list:
    """
    Fetch all history items belonging to a style.
    Returns list of dicts matching the style_items table columns.
    """
    cursor.execute("""
        SELECT num, module, operation, op_params, enabled,
               blendop_params, blendop_version, multi_priority,
               multi_name
        FROM style_items
        WHERE styleid = ?
        ORDER BY num
    """, (style_id,))
    cols = ["num", "module", "operation", "op_params", "enabled",
            "blendop_params", "blendop_version", "multi_priority", "multi_name"]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def get_film_roll_id(cursor: sqlite3.Cursor, folder: Path) -> int:
    """Look up the film roll id for a folder path."""
    folder_str = str(folder).rstrip("/")
    cursor.execute(
        "SELECT id FROM film_rolls WHERE folder = ?",
        (folder_str,)
    )
    row = cursor.fetchone()
    if not row:
        print(f"ERROR: Folder not registered in darktable: {folder}")
        print("  Run dt_import_all first to register your albums.")
        sys.exit(1)
    return row[0]


def get_rated_images(cursor: sqlite3.Cursor, film_id: int, min_rating: int) -> list:
    """
    Return all images in a film roll with rating >= min_rating.
    Rating is stored in lower 3 bits of flags field.
    """
    # Darktable stores ratings as (user_rating - 1) in the flags field
    # So 4 stars in UI = flags & 7 = 3, 5 stars = flags & 7 = 4
    # We subtract 1 from min_rating to match what's actually in the DB
    db_min_rating = max(0, min_rating - 1)
    cursor.execute("""
        SELECT id, filename, flags
        FROM images
        WHERE film_id = ?
        AND (flags & 7) >= ?
    """, (film_id, db_min_rating))
    return cursor.fetchall()


def style_already_applied(cursor: sqlite3.Cursor, image_id: int, style_items: list) -> bool:
    """
    Check if the first operation from the style already exists in this
    image's history stack to avoid double-applying.
    """
    if not style_items:
        return False
    first_op = style_items[0]["operation"]
    cursor.execute("""
        SELECT imgid FROM history
        WHERE imgid = ? AND operation = ?
        LIMIT 1
    """, (image_id, first_op))
    return cursor.fetchone() is not None


def get_next_history_num(cursor: sqlite3.Cursor, image_id: int) -> int:
    """Get the next available history stack number for an image."""
    cursor.execute(
        "SELECT COALESCE(MAX(num), -1) + 1 FROM history WHERE imgid = ?",
        (image_id,)
    )
    return cursor.fetchone()[0]


def apply_style_to_image(cursor: sqlite3.Cursor, image_id: int, style_items: list):
    """
    Write style history items into the image's history stack,
    then mark the image as changed so darktable reloads it.
    """
    start_num = get_next_history_num(cursor, image_id)

    for i, item in enumerate(style_items):
        cursor.execute("""
            INSERT INTO history (
                imgid, num, module, operation, op_params, enabled,
                blendop_params, blendop_version, multi_priority, multi_name,
                multi_name_hand_edited
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            image_id,
            start_num + i,
            item["module"],
            item["operation"],
            item["op_params"],
            item["enabled"],
            item["blendop_params"],
            item["blendop_version"],
            item["multi_priority"],
            item["multi_name"],
        ))

    # Update history_end so darktable knows to use the new stack length
    cursor.execute("""
        UPDATE images
        SET history_end = (SELECT COUNT(*) FROM history WHERE imgid = ?),
            change_timestamp = ?
        WHERE id = ?
    """, (image_id, int(datetime.now().timestamp()), image_id))

# ── Core ──────────────────────────────────────────────────────────────────────

def apply_style(folder: Path, style_name: str, min_rating: int):
    data_conn = connect_data()
    lib_conn  = connect_library()

    data_cursor = data_conn.cursor()
    lib_cursor  = lib_conn.cursor()

    style_id    = get_style(data_cursor, style_name)
    style_items = get_style_items(data_cursor, style_id)

    if not style_items:
        print(f"ERROR: Style '{style_name}' has no items — is it empty?")
        sys.exit(1)

    film_id = get_film_roll_id(lib_cursor, folder)
    images  = get_rated_images(lib_cursor, film_id, min_rating)

    if not images:
        print(f"No images rated {min_rating}+ stars found in {folder}")
        sys.exit(0)

    print(f"\n  Style      : {style_name}  ({len(style_items)} operation(s))")
    print(f"  Film roll  : {folder.parent.name}/raw/")
    print(f"  Min rating : {min_rating}★")
    print(f"  Images     : {len(images)} eligible\n")

    backup_db()

    applied = 0
    skipped = 0

    for img_id, filename, flags in images:
        rating    = (flags & 7) + 1   # convert DB value back to UI star rating
        if style_already_applied(lib_cursor, img_id, style_items):
            print(f"  skip  ★{rating}  {filename}  (style already applied)")
            skipped += 1
            continue
        apply_style_to_image(lib_cursor, img_id, style_items)
        print(f"  done  ★{rating}  {filename}")
        applied += 1

    lib_conn.commit()
    lib_conn.close()
    data_conn.close()

    print(f"\n── Summary ───────────────────────────────")
    print(f"  Applied  : {applied}")
    print(f"  Skipped  : {skipped}  (already had style)")
    print(f"\nReload the film roll in darktable to see changes.")

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Apply a darktable style to all 4+ star images in a film roll"
    )
    parser.add_argument(
        "folder",
        nargs="?",
        help="Path to the raw/ folder (film roll)"
    )
    parser.add_argument(
        "style",
        nargs="?",
        help="Name of the darktable style to apply"
    )
    parser.add_argument(
        "--min-rating",
        type=int,
        default=4,
        choices=[1, 2, 3, 4, 5],
        help="Minimum star rating to process (default: 4)"
    )
    parser.add_argument(
        "--list-styles",
        action="store_true",
        help="List all available styles in your darktable library"
    )
    args = parser.parse_args()

    if args.list_styles:
        conn = connect_data()
        list_styles(conn)
        conn.close()
        sys.exit(0)

    if not args.folder or not args.style:
        parser.print_help()
        sys.exit(1)

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"ERROR: Not a directory: {folder}")
        sys.exit(1)

    apply_style(folder, args.style, args.min_rating)


if __name__ == "__main__":
    main()
