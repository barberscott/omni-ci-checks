#!/usr/bin/env python3
"""Assemble one combined validation sticky-comment from per-check diffs.

Each Omni validation job uploads its `diff.json` (from diff-findings.py) as an
artifact named `omni-section-NN-<check>`. The validation-summary job downloads
them under --sections-dir (one subdirectory per artifact) and runs this script
to render a single comment:

  1. A status TABLE — one row per check (status, net-new counts, resolved,
     branch/base totals).
  2. Collapsible per-check details for any check with net-new findings.

Per-job results are passed via env (R_MODEL / R_CONTENT / R_REFERENCE / R_AI)
so the status column is accurate even for skipped checks (which have no
diff.json because a prerequisite — model-validate — failed).
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

# (artifact dir, human label, env var holding the job's result)
SECTIONS = [
    ("omni-section-01-model", "Model validation", "R_MODEL"),
    ("omni-section-02-content", "Content validation", "R_CONTENT"),
    ("omni-section-03-reference", "Reference queries", "R_REFERENCE"),
    ("omni-section-04-ai", "AI evals", "R_AI"),
    ("omni-section-05-hygiene", "Shared-model hygiene", "R_HYGIENE"),
]

SEV_EMOJI = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}
SEV_ORDER = {"error": 0, "warning": 1, "info": 2}
STATUS = {
    "success": "✅ Pass",
    "failure": "❌ Fail",
    "skipped": "⏭️ Skipped",
    "cancelled": "🚫 Cancelled",
    "canceled": "🚫 Cancelled",
}


def _cell(text) -> str:
    s = ("" if text is None else str(text)).strip()
    s = re.sub(r"\s*\n\s*", " ", s)
    return s.replace("|", "\\|")


def _net_new_counts(diff: dict) -> str:
    e = len(diff.get("errors", []))
    w = len(diff.get("warnings", []))
    i = len(diff.get("infos", []))
    parts = []
    if e:
        parts.append(f"{e} err")
    if w:
        parts.append(f"{w} warn")
    if i:
        parts.append(f"{i} info")
    return ", ".join(parts) if parts else "—"


def _load(base: Path, artifact: str) -> dict | None:
    p = base / artifact / "diff.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sections-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    base = Path(args.sections_dir)

    rows: list[str] = []
    details: list[str] = []

    for artifact, label, env_key in SECTIONS:
        result = (os.environ.get(env_key, "") or "").strip().lower()
        status = STATUS.get(result, f"❔ {result or 'unknown'}")
        diff = _load(base, artifact)

        if diff is None:
            # Skipped (or failed before producing a diff): no counts to show.
            rows.append(f"| {label} | {status} | — | — | — |")
            continue

        new = _net_new_counts(diff)
        fixed = len(diff.get("fixed", []))
        bt = diff.get("branch_total", "?")
        bs = diff.get("base_total", "?")
        rows.append(f"| {label} | {status} | {new} | {fixed or '—'} | {bt} / {bs} |")

        net_new = diff.get("net_new", [])
        if net_new:
            net_new = sorted(
                net_new, key=lambda x: SEV_ORDER.get(x.get("severity", "info"), 3)
            )
            block = [
                "<details>",
                f"<summary><b>{label}</b> — {len(net_new)} net-new</summary>",
                "",
                "| Sev | Location | Finding |",
                "|:--:|:--|:--|",
            ]
            for it in net_new:
                emoji = SEV_EMOJI.get(it.get("severity", ""), "•")
                loc = _cell(it.get("location"))
                msg = _cell(it.get("message"))
                block.append(f"| {emoji} | {('`' + loc + '`') if loc else '—'} | {msg} |")
            block += ["", "</details>"]
            details.append("\n".join(block))

    parts: list[str] = []
    parts.append("## Omni validation summary")
    parts.append("")
    parts.append(
        "_Combined status of the model, content, reference-query, and AI-query "
        "checks. Each check still owns its pass/fail status above._"
    )
    parts.append("")
    parts.append("| Check | Status | Net-new | Resolved | Branch / Base |")
    parts.append("|:--|:--:|:--|:--:|:--:|")
    parts.extend(rows)

    if details:
        parts.append("")
        parts.append("### Details")
        for block in details:
            parts.append("")
            parts.append(block)

    Path(args.out).write_text("\n".join(parts).rstrip() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
