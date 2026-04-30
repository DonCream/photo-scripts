#!/usr/bin/env python3
"""
contact_sheet_wrapper — Python wrapper for contact_sheet.py
Designed to be called from digiKam's Tools menu or right-click menu.
If no folder is passed as argument, opens a folder picker dialog via zenity.

Install:
    cp contact_sheet_wrapper.py ~/.local/bin/contact_sheet_wrapper
    chmod +x ~/.local/bin/contact_sheet_wrapper

Usage:
    contact_sheet_wrapper                    # opens folder picker
    contact_sheet_wrapper /path/to/folder    # runs directly on folder
"""

import subprocess
import sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

SCRIPT = Path.home() / ".local/bin/photo_scripts/contactSheet/contact_sheet.py"

# ── Zenity helpers ────────────────────────────────────────────────────────────

def zenity_error(message: str):
    subprocess.run([
        "zenity", "--error",
        "--title=Contact Sheet",
        f"--text={message}",
    ], capture_output=True)


def zenity_info(message: str, timeout: int = 4):
    subprocess.run([
        "zenity", "--info",
        "--title=Contact Sheet",
        f"--text={message}",
        f"--timeout={timeout}",
    ], capture_output=True)


def zenity_folder_picker() -> str | None:
    result = subprocess.run([
        "zenity", "--file-selection",
        "--directory",
        "--title=Select folder for contact sheet",
        f"--filename={Path.home()}/Pictures/",
    ], capture_output=True, text=True)
    folder = result.stdout.strip()
    return folder if folder else None


def zenity_entry(prompt: str, default: str = "") -> str | None:
    result = subprocess.run([
        "zenity", "--entry",
        "--title=Contact Sheet",
        f"--text={prompt}",
        f"--entry-text={default}",
    ], capture_output=True, text=True)
    # Return None if user cancelled (non-zero exit), empty string if left blank
    if result.returncode != 0:
        return None
    return result.stdout.strip()

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Check the main script exists
    if not SCRIPT.exists():
        zenity_error(f"contact_sheet.py not found at:\n{SCRIPT}")
        sys.exit(1)

    # Get folder from argument or picker
    if len(sys.argv) > 1:
        folder = Path(sys.argv[1]).expanduser().resolve()
    else:
        picked = zenity_folder_picker()
        if not picked:
            sys.exit(0)   # user cancelled
        folder = Path(picked).resolve()

    if not folder.is_dir():
        zenity_error(f"Not a valid folder:\n{folder}")
        sys.exit(1)

    # Ask for optional title
    title = zenity_entry(
        "Enter a title for the contact sheet\n(leave blank to use folder name):",
        default=folder.name,
    )

    if title is None:
        sys.exit(0)   # user cancelled title dialog

    # Build command
    cmd = ["python3", str(SCRIPT), str(folder)]
    if title:
        cmd += ["--title", title]

    # Run contact_sheet.py
    result = subprocess.run(cmd)

    if result.returncode == 0:
        zenity_info("Contact sheet generated and opened in browser.")
    else:
        zenity_error("Something went wrong generating the contact sheet.\nCheck the terminal for details.")


if __name__ == "__main__":
    main()
