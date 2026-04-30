#!/usr/bin/env python3
"""
backup_scripts — Commit and push the photo_scripts repo to GitHub.

The repo lives at ~/.local/bin/photo_scripts/ — this script can be run
from anywhere; it always operates on that directory.

Usage:
    backup_scripts             commit and push
    backup_scripts --status    show git status, exit
    backup_scripts --no-push   commit only, skip push
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path.home() / ".local" / "bin" / "photo_scripts"


def git(*args):
    """Run a git command inside the repo. Returns (returncode, stdout, stderr)."""
    r = subprocess.run(
        ["git", *args], cwd=REPO,
        capture_output=True, text=True
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def main():
    p = argparse.ArgumentParser(description="Back up photo_scripts to GitHub.")
    p.add_argument("--status",  action="store_true", help="show git status and exit")
    p.add_argument("--no-push", action="store_true", help="commit only, don't push")
    args = p.parse_args()

    if not (REPO / ".git").is_dir():
        sys.exit(f"Not a git repo: {REPO}")

    if args.status:
        _, out, _ = git("status", "--short", "--branch")
        print(out or "clean")
        return

    # Anything to commit?
    _, changes, _ = git("status", "--porcelain")
    if not changes:
        print("Nothing to commit — already up to date.")
        return

    git("add", "-A")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    code, _, err = git("commit", "-m", f"backup: {ts}")
    if code != 0:
        sys.exit(f"Commit failed: {err}")
    print(f"Committed at {ts}")

    if args.no_push:
        return

    code, _, err = git("push")
    if code != 0:
        sys.exit(f"Push failed: {err}")
    print("Pushed.")


if __name__ == "__main__":
    main()
