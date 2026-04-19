#!/usr/bin/env python3
"""
shoot_report — Generate a summary report from cull_results.json.
Prints keeper rate, rating breakdown, and average AI scores.
Optionally saves report as a text file alongside the JSON.

Usage:
    shoot_report                              # prompts for json file
    shoot_report /path/to/cull_results.json
    shoot_report /path/to/cull_results.json --save
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# ── Input ─────────────────────────────────────────────────────────────────────

def pick_file() -> Path:
    print("\nDrag and drop your cull_results.json into the terminal and press Enter:")
    path = input("File: ").strip().strip("'\"")
    if not path:
        print("No file entered.")
        sys.exit(0)
    return Path(path).expanduser().resolve()

# ── Report ────────────────────────────────────────────────────────────────────

def bar(count: int, total: int, width: int = 20) -> str:
    filled = int(width * count / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def build_report(data: list, source: str) -> str:
    photos  = [r for r in data if r.get("type") in ("raw", "jpg") and "rating" in r]
    videos  = [r for r in data if r.get("type") == "video"]
    errors  = [r for r in data if "error" in r]
    total   = len(photos)

    lines = []
    lines.append("═" * 52)
    lines.append(f"  SHOOT REPORT")
    lines.append(f"  {source}")
    lines.append("═" * 52)
    lines.append("")

    # Overview
    lines.append("OVERVIEW")
    lines.append("─" * 52)
    lines.append(f"  Total photos   : {total}")
    lines.append(f"  Videos         : {len(videos)}")
    if errors:
        lines.append(f"  Errors         : {len(errors)}")

    keepers     = [r for r in photos if r.get("rating", 0) >= 4]
    keeper_rate = len(keepers) / total * 100 if total else 0
    lines.append(f"  Keepers (4-5★) : {len(keepers)}  ({keeper_rate:.1f}%)")
    lines.append("")

    # Rating breakdown
    lines.append("RATING BREAKDOWN")
    lines.append("─" * 52)
    counts = Counter(r.get("rating", 0) for r in photos)
    for star in range(5, 0, -1):
        count = counts.get(star, 0)
        pct   = count / total * 100 if total else 0
        lines.append(f"  ★{star}  {bar(count, total)}  {count:>4}  ({pct:>5.1f}%)")
    lines.append("")

    # AI score averages
    focus_scores = [r["focus_score"] for r in photos
                    if isinstance(r.get("focus_score"), (int, float))]
    comp_scores  = [r["composition_score"] for r in photos
                    if isinstance(r.get("composition_score"), (int, float))]
    expr_scores  = [r["expression_score"] for r in photos
                    if isinstance(r.get("expression_score"), (int, float))]

    if any([focus_scores, comp_scores, expr_scores]):
        lines.append("AI SCORE AVERAGES")
        lines.append("─" * 52)
        if focus_scores:
            avg = sum(focus_scores) / len(focus_scores)
            lines.append(f"  Focus       : {avg:.2f} / 5.00  {bar(int(avg*4), 20)}")
        if comp_scores:
            avg = sum(comp_scores) / len(comp_scores)
            lines.append(f"  Composition : {avg:.2f} / 5.00  {bar(int(avg*4), 20)}")
        if expr_scores:
            avg = sum(expr_scores) / len(expr_scores)
            lines.append(f"  Expression  : {avg:.2f} / 5.00  {bar(int(avg*4), 20)}")
        lines.append("")

    # File type breakdown
    raw_count  = len([r for r in photos if r.get("type") == "raw"])
    jpg_count  = len([r for r in photos if r.get("type") == "jpg"])
    lines.append("FILE TYPES")
    lines.append("─" * 52)
    lines.append(f"  RAW  : {raw_count}")
    lines.append(f"  JPEG : {jpg_count}")
    lines.append("")

    # Top rated reasons
    top = sorted(
        [r for r in photos if r.get("rating", 0) == 5 and r.get("reason")],
        key=lambda r: r.get("rating", 0), reverse=True
    )[:3]
    if top:
        lines.append("TOP RATED SHOTS")
        lines.append("─" * 52)
        for r in top:
            lines.append(f"  {r.get('new_name') or r.get('file', '')}")
            lines.append(f"    \"{r['reason']}\"")
        lines.append("")

    lines.append("═" * 52)
    return "\n".join(lines)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate a shoot summary from cull_results.json"
    )
    parser.add_argument("file",   nargs="?", default="",
                        help="Path to cull_results.json")
    parser.add_argument("--save", action="store_true",
                        help="Save report as shoot_report.txt alongside the JSON")
    args = parser.parse_args()

    json_path = Path(args.file).expanduser().resolve() if args.file \
                else pick_file()

    if not json_path.exists():
        print(f"File not found: {json_path}")
        sys.exit(1)

    with open(json_path) as f:
        data = json.load(f)

    report = build_report(data, json_path.parent.name)
    print("\n" + report)

    if args.save:
        out_path = json_path.parent / "shoot_report.txt"
        out_path.write_text(report)
        print(f"\nReport saved → {out_path}")


if __name__ == "__main__":
    main()
