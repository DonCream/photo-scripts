#!/usr/bin/env python3
"""
backup_scripts — Commit and push all photo workflow scripts to git.
Supports two modes:
  - per-folder: each script folder is its own git repo (default)
  - monorepo:   all script folders combined into one repo at ~/photo-scripts/

Usage:
    backup_scripts                          # per-folder backup
    backup_scripts --status                 # show git status of all folders
    backup_scripts --no-push                # commit only, don't push
    backup_scripts --add-remote URL         # set remote for all folders
    backup_scripts --monorepo               # use single combined repo
    backup_scripts --monorepo --init ~/photo-scripts  # set up monorepo
"""

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

BIN_DIR      = Path.home() / ".local" / "bin" / "photo_scripts"
MONOREPO_DIR = Path.home() / "photo-scripts"

GITIGNORE_CONTENT = """# Python
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
*.egg-info/
dist/
build/

# Virtual environments
venv/
.venv/
env/

# OS files
.DS_Store
Thumbs.db
.directory

# Logs and temp files
*.log
*.tmp
*.bak
*.swp

# Large media files — scripts only, not test images
*.jpg
*.jpeg
*.png
*.tiff
*.tif
*.webp
*.cr2
*.cr3
*.dng
*.nef
*.arw
*.mp4
*.mov
*.avi
*.mkv

# Database backups
library_backup_*.db

# API keys — never commit these
.env
*.env
secrets.py
"""


def find_script_folders() -> list:
    """
    Find all subfolders of ~/.local/bin/photo_scripts/ that contain .py files.
    Structure: photo_scripts/dtCull/cull_proc.py etc.
    """
    if not BIN_DIR.exists():
        return []
    return sorted(
        d for d in BIN_DIR.iterdir()
        if d.is_dir()
        and not d.name.startswith(".")
        and any(f.suffix == ".py" for f in d.iterdir() if f.is_file())
    )

# ── Git helpers ───────────────────────────────────────────────────────────────

def run(cmd: list, cwd: Path, capture: bool = True) -> tuple:
    result = subprocess.run(
        cmd, cwd=str(cwd),
        capture_output=capture,
        text=True
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def git_init(folder: Path):
    if not (folder / ".git").exists():
        run(["git", "init"], folder)
        # Set default branch to main
        run(["git", "checkout", "-b", "main"], folder)
        print(f"    Initialized git repo")


def write_gitignore(folder: Path):
    gi_path = folder / ".gitignore"
    if not gi_path.exists():
        gi_path.write_text(GITIGNORE_CONTENT)
        print(f"    Created .gitignore")


def git_status(folder: Path) -> str:
    code, out, _ = run(["git", "status", "--short"], folder)
    return out


def has_changes(folder: Path) -> bool:
    return bool(git_status(folder))


def git_add_all(folder: Path):
    run(["git", "add", "-A"], folder)


def git_commit(folder: Path, message: str) -> bool:
    code, out, err = run(["git", "commit", "-m", message], folder)
    return code == 0


def git_push(folder: Path) -> tuple:
    code, remote, _ = run(["git", "remote", "get-url", "origin"], folder)
    if code != 0:
        return False, "no remote configured — run --add-remote first"
    # Try main then master
    code, out, err = run(["git", "push", "-u", "origin", "main"], folder)
    if code != 0:
        code, out, err = run(["git", "push", "-u", "origin", "master"], folder)
    return code == 0, err if code != 0 else "pushed"


def add_remote(folder: Path, url: str):
    code, _, _ = run(["git", "remote", "get-url", "origin"], folder)
    if code == 0:
        run(["git", "remote", "set-url", "origin", url], folder)
        print(f"    Updated remote → {url}")
    else:
        run(["git", "remote", "add", "origin", url], folder)
        print(f"    Added remote → {url}")


def get_remote(folder: Path) -> str:
    code, url, _ = run(["git", "remote", "get-url", "origin"], folder)
    return url if code == 0 else ""


def get_branch(folder: Path) -> str:
    code, branch, _ = run(["git", "branch", "--show-current"], folder)
    return branch if code == 0 else "unknown"

# ── Per-folder mode ───────────────────────────────────────────────────────────

def backup_folder(folder: Path, push: bool) -> dict:
    result = {"name": folder.name, "committed": False, "pushed": False, "message": ""}

    if not folder.exists():
        result["message"] = "not found — skipping"
        return result

    git_init(folder)
    write_gitignore(folder)

    if not has_changes(folder):
        result["message"] = "nothing to commit"
        return result

    git_add_all(folder)
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M")
    ok  = git_commit(folder, f"backup: {ts}")

    if ok:
        result["committed"] = True
        result["message"]   = f"committed at {ts}"
        if push:
            pushed, msg = git_push(folder)
            result["pushed"]  = pushed
            result["message"] += f"  {'→ pushed' if pushed else f'(push failed: {msg})'}"
    else:
        result["message"] = "commit failed"

    return result


def run_per_folder_backup(folders: list, push: bool):
    print(f"\nPer-folder backup — {len(folders)} folder(s)\n")
    committed = pushed = skipped = 0

    for folder in folders:
        print(f"  {folder.name}/")
        result = backup_folder(folder, push)
        print(f"    {result['message']}")
        if result["committed"]: committed += 1
        if result["pushed"]:    pushed    += 1
        if not result["committed"]: skipped += 1

    print(f"\n── Summary ─────────────────────────────────")
    print(f"  Committed : {committed}")
    print(f"  Pushed    : {pushed}")
    print(f"  Skipped   : {skipped}  (clean or missing)")


def show_status(folders: list):
    print(f"\n── Git status ──────────────────────────────")
    for folder in folders:
        if not folder.exists():
            print(f"\n  {folder.name}/  — not found")
            continue
        if not (folder / ".git").exists():
            print(f"\n  {folder.name}/  — not a git repo yet")
            continue
        status = git_status(folder)
        branch = get_branch(folder)
        remote = get_remote(folder)
        print(f"\n  {folder.name}/  [{branch}]")
        if remote:
            print(f"    remote : {remote}")
        if status:
            for line in status.splitlines():
                print(f"    {line}")
        else:
            print(f"    clean")

# ── Monorepo mode ─────────────────────────────────────────────────────────────

def init_monorepo(folders: list, repo_dir: Path):
    """
    Copy all script folders into a single repo directory and initialize git.
    Structure: ~/photo-scripts/cull/ ~/photo-scripts/waterMark/ etc.
    """
    print(f"\nInitializing monorepo at {repo_dir}\n")
    repo_dir.mkdir(parents=True, exist_ok=True)

    # Write root .gitignore
    gi = repo_dir / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE_CONTENT)
        print(f"  Created .gitignore")

    # Write README
    readme = repo_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Photo Workflow Scripts\n\n"
            "WebJelly Studios photo workflow automation scripts.\n\n"
            "## Folders\n\n" +
            "\n".join(f"- `{f.name}/`" for f in folders) + "\n"
        )
        print(f"  Created README.md")

    # Copy each script subfolder into monorepo
    for folder in folders:
        dest = repo_dir / folder.name
        if dest.exists():
            shutil.rmtree(str(dest))
        shutil.copytree(str(folder), str(dest),
                        ignore=shutil.ignore_patterns(
                            "__pycache__", "*.pyc", ".git",
                            "*.jpg", "*.jpeg", "*.png", "*.cr2", "*.cr3",
                            "*.dng", "*.nef", "*.mp4", "library_backup_*.db"
                        ))
        print(f"  Copied {folder.name}/")

    # Init git
    git_init(repo_dir)
    write_gitignore(repo_dir)
    git_add_all(repo_dir)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    ok = git_commit(repo_dir, f"initial monorepo commit: {ts}")
    if ok:
        print(f"\n  Monorepo initialized and committed.")
        print(f"  Next: add a remote with --add-remote URL --monorepo")
    else:
        print(f"\n  Nothing new to commit — monorepo is up to date.")


def backup_monorepo(folders: list, repo_dir: Path, push: bool):
    """Sync latest script files into monorepo and commit."""
    if not repo_dir.exists():
        print(f"ERROR: Monorepo not found at {repo_dir}")
        print(f"  Run: backup_scripts --monorepo --init to set it up first.")
        sys.exit(1)

    print(f"\nMonorepo backup → {repo_dir}\n")

    # Sync each subfolder
    for folder in folders:
        dest = repo_dir / folder.name
        dest.mkdir(exist_ok=True)
        for fpath in folder.iterdir():
            if fpath.is_file() and fpath.suffix == ".py":
                shutil.copy2(str(fpath), str(dest / fpath.name))
        print(f"  Synced {folder.name}/")

    if not has_changes(repo_dir):
        print("\n  Nothing changed — monorepo is up to date.")
        return

    git_add_all(repo_dir)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    ok = git_commit(repo_dir, f"backup: {ts}")

    if ok:
        print(f"\n  Committed at {ts}")
        if push:
            pushed, msg = git_push(repo_dir)
            if pushed:
                print(f"  Pushed to remote.")
            else:
                print(f"  Push failed: {msg}")
    else:
        print("\n  Commit failed.")

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Back up photo workflow scripts to git"
    )
    parser.add_argument("--status",     action="store_true",
                        help="Show git status of all script folders")
    parser.add_argument("--no-push",    action="store_true",
                        help="Commit only, don't push to remote")
    parser.add_argument("--add-remote", metavar="URL",
                        help="Add or update git remote URL")
    parser.add_argument("--monorepo",   action="store_true",
                        help="Use single combined repo instead of per-folder repos")
    parser.add_argument("--init",       action="store_true",
                        help="Initialize monorepo (use with --monorepo)")
    parser.add_argument("--repo-dir",   default=str(MONOREPO_DIR),
                        help=f"Monorepo directory (default: {MONOREPO_DIR})")
    args = parser.parse_args()

    folders   = find_script_folders()
    repo_dir  = Path(args.repo_dir).expanduser().resolve()

    if not folders:
        print(f"No script folders found in {BIN_DIR}")
        sys.exit(0)

    print(f"Found {len(folders)} script folder(s) in {BIN_DIR}")

    # ── Monorepo mode ─────────────────────────────────────────────────────────
    if args.monorepo:
        if args.add_remote:
            git_init(repo_dir)
            add_remote(repo_dir, args.add_remote)
            print("Done.")
            return
        if args.init:
            init_monorepo(folders, repo_dir)
            return
        if args.status:
            show_status([repo_dir])
            return
        backup_monorepo(folders, repo_dir, push=not args.no_push)
        return

    # ── Per-folder mode ───────────────────────────────────────────────────────
    if args.add_remote:
        print(f"\nAdding remote to all folders...\n")
        for folder in folders:
            print(f"  {folder.name}/")
            git_init(folder)
            add_remote(folder, args.add_remote)
        print("\nDone. Run backup_scripts to commit and push.")
        return

    if args.status:
        show_status(folders)
        return

    run_per_folder_backup(folders, push=not args.no_push)


if __name__ == "__main__":
    main()

