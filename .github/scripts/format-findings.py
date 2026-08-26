#!/usr/bin/env python3
"""Render a diff-findings JSON document as a sticky-comment markdown body.

Usage:
    format-findings.py <diff.json> --title "Model validation" [--out body.md]
"""

from __future__ import annotations

import argparse
import json
import sys

SEV_EMOJI = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}


def render(diff: dict, title: str) -> str:
    base_total = diff.get("base_total", 0)
    branch_total = diff.get("branch_total", 0)
    net_new = diff.get("net_new", [])
    fixed = diff.get("fixed", [])
    errors = diff.get("errors", [])
    warnings = diff.get("warnings", [])
    infos = diff.get("infos", [])

    lines: list[str] = []
    lines.append(f"## {title}")
    lines.append("")

    if not net_new and not fixed:
        lines.append(f"✅ No net-new issues. ({branch_total} on branch, {base_total} on base)")
        return "\n".join(lines)

    summary_bits = []
    if errors:
        summary_bits.append(f"{len(errors)} new error(s)")
    if warnings:
        summary_bits.append(f"{len(warnings)} new warning(s)")
    if infos:
        summary_bits.append(f"{len(infos)} new info(s)")
    if fixed:
        summary_bits.append(f"{len(fixed)} resolved")
    lines.append("**Summary:** " + ", ".join(summary_bits) if summary_bits else "")
    lines.append(f"_Totals — branch: {branch_total}, base: {base_total}._")
    lines.append("")

    for label, items in (("New errors", errors), ("New warnings", warnings), ("New infos", infos)):
        if not items:
            continue
        lines.append(f"### {label}")
        lines.append("")
        for it in items[:50]:
            emoji = SEV_EMOJI.get(it.get("severity", ""), "•")
            loc = it.get("location") or ""
            msg = (it.get("message") or "").strip().replace("\n", " ")
            if len(msg) > 400:
                msg = msg[:400] + "…"
            lines.append(f"- {emoji} `{loc}` — {msg}" if loc else f"- {emoji} {msg}")
        if len(items) > 50:
            lines.append(f"- _…and {len(items) - 50} more._")
        lines.append("")

    if fixed:
        lines.append(f"<details><summary>✅ Resolved on branch ({len(fixed)})</summary>")
        lines.append("")
        for it in fixed[:50]:
            loc = it.get("location") or ""
            msg = (it.get("message") or "").strip().replace("\n", " ")[:200]
            lines.append(f"- `{loc}` — {msg}" if loc else f"- {msg}")
        if len(fixed) > 50:
            lines.append(f"- _…and {len(fixed) - 50} more._")
        lines.append("")
        lines.append("</details>")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("diff", help="Path to diff JSON")
    ap.add_argument("--title", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    with open(args.diff) as f:
        diff = json.load(f)

    body = render(diff, args.title)
    if args.out:
        with open(args.out, "w") as f:
            f.write(body)
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
