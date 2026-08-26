#!/usr/bin/env python3
"""Resolve an /omni-fix selection against the BP comment's embedded findings.

Reads the PR's issue comments (JSON array, e.g. from `gh api .../comments`),
finds the best-practices sticky comment, decodes its hidden
`<!-- omni-bp-data:v1 <b64> -->` block, applies a SHA guard, then resolves the
selection string into the chosen findings.

Selection grammar (comma-separated, unioned, deduped):
  - positional:  1.1            single finding
                 3.3-3.5        range within a file (same file #)
                 2.*            every finding in file #2
                 *  /  all      every finding
  - severity:    errors | warnings | infos   (or sev:error / sev:warning / sev:info)
  - rule:        rule:raw-column-sql          (or a bare known rule id)

Exit codes:
  0  ok (selected.json written)
  3  no BP data block found (run a review first)
  4  stale: data head_sha != --current-sha (branch moved)
  5  nothing selected (all selectors empty/invalid)
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

SEV_WORDS = {
    "errors": "error", "error": "error",
    "warnings": "warning", "warning": "warning", "warns": "warning", "warn": "warning",
    "infos": "info", "info": "info",
}
DATA_RE = re.compile(r"<!--\s*omni-bp-data:v1\s+([A-Za-z0-9+/=]+)\s*-->")
BP_MARKER = "<!-- omni-best-practices -->"


def _decode_data(comments: list[dict]) -> dict | None:
    # Prefer the dedicated BP sticky comment; fall back to any comment with a block.
    candidates = [c for c in comments if BP_MARKER in (c.get("body") or "")]
    candidates += [c for c in comments if c not in candidates]
    for c in candidates:
        m = DATA_RE.search(c.get("body") or "")
        if m:
            try:
                return json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
            except Exception:
                continue
    return None


def _expand(selection: str, findings: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    by_id = {f["id"]: f for f in findings}
    rules = {f.get("rule") for f in findings if f.get("rule")}
    chosen: dict[str, dict] = {}
    invalid: list[str] = []
    empty: list[str] = []

    tokens = [t.strip() for t in re.split(r"[,\s]+", selection.strip()) if t.strip()]
    for tok in tokens:
        low = tok.lower()
        matched: list[dict] = []

        if low in ("*", "all"):
            matched = list(findings)
        elif re.fullmatch(r"\d+\.\*", tok):
            fpfx = tok.split(".")[0] + "."
            matched = [f for f in findings if f["id"].startswith(fpfx)]
        elif re.fullmatch(r"\d+\.\d+", tok):
            if tok in by_id:
                matched = [by_id[tok]]
        elif re.fullmatch(r"\d+\.\d+-\d+", tok):
            head, rng = tok.split(".")
            lo, hi = rng.split("-")
            if int(lo) <= int(hi):
                for n in range(int(lo), int(hi) + 1):
                    fid = f"{head}.{n}"
                    if fid in by_id:
                        matched.append(by_id[fid])
        elif low in SEV_WORDS or low.startswith("sev:"):
            sev = SEV_WORDS.get(low) or SEV_WORDS.get(low.split(":", 1)[1], None)
            if sev:
                matched = [f for f in findings if f.get("severity") == sev]
            else:
                invalid.append(tok)
                continue
        elif low.startswith("rule:") or tok in rules:
            rule = tok.split(":", 1)[1] if ":" in tok else tok
            if rule in rules:
                matched = [f for f in findings if f.get("rule") == rule]
            else:
                invalid.append(tok)
                continue
        else:
            invalid.append(tok)
            continue

        if matched:
            for f in matched:
                chosen[f["id"]] = f
        else:
            empty.append(tok)

    ordered = sorted(
        chosen.values(),
        key=lambda f: tuple(int(p) for p in f["id"].split(".")),
    )
    return ordered, invalid, empty


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comments", required=True, help="JSON array of PR issue comments")
    ap.add_argument("--selection", required=True)
    ap.add_argument("--current-sha", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default="", help="optional path for a human summary")
    args = ap.parse_args()

    def _write_report(text: str) -> None:
        if args.report:
            Path(args.report).write_text(text + "\n")

    comments = json.loads(Path(args.comments).read_text())
    data = _decode_data(comments)
    if data is None:
        _write_report(
            "## /omni-fix\n\n❌ No best-practices findings are available on this PR yet. "
            "Wait for the **Best practices review** check to post its comment, then re-run `/omni-fix`."
        )
        print("::error::No omni-bp-data block found — run the best-practices review first.", file=sys.stderr)
        return 3

    data_sha = (data.get("head_sha") or "").strip()
    cur = (args.current_sha or "").strip()
    if cur and data_sha and data_sha != cur:
        _write_report(
            f"## /omni-fix\n\n🚫 The findings are **stale** — the branch moved "
            f"(`{data_sha[:7]}` → `{cur[:7]}`) since the review. Re-run `/omni-fix` "
            f"once the new **Best practices review** comment lands."
        )
        print(f"::error::Stale findings: comment head_sha {data_sha[:7]} != current {cur[:7]}.", file=sys.stderr)
        return 4

    findings = data.get("findings") or []
    selected, invalid, empty = _expand(args.selection, findings)
    Path(args.out).write_text(json.dumps({"selected": selected, "invalid": invalid, "empty": empty}, indent=2))

    notes = []
    if empty:
        notes.append("matched nothing: " + ", ".join(f"`{x}`" for x in empty))
    if invalid:
        notes.append("invalid: " + ", ".join(f"`{x}`" for x in invalid))

    if not selected:
        msg = "## /omni-fix\n\n⚠️ Nothing selected to fix."
        if notes:
            msg += " (" + "; ".join(notes) + ")"
        msg += "\n\nUse IDs like `1.1`, ranges `3.3-3.5`, `2.*`, a severity (`errors`), a rule (`rule:raw-column-sql`), or `all`."
        _write_report(msg)
        print(msg, file=sys.stderr)
        return 5

    summary = "Selected " + ", ".join(f["id"] for f in selected)
    if notes:
        summary += " (" + "; ".join(notes) + ")"
    _write_report("## /omni-fix\n\n" + summary)
    print(summary, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
