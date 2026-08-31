#!/usr/bin/env python3
"""Render best-practices JSON (from Claude) as a sticky-comment markdown body.

Input shape (matches .github/schemas/best-practices.json):

    {
      "summary": "...",
      "findings": [
        { "file": "...", "line": 42, "severity": "warning",
          "rule": "missing-description", "message": "...", "suggestion": "..." },
        ...
      ]
    }

Usage:
    format-best-practices.py --in findings.json --out body.md
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path

SEV_EMOJI = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}
SEV_ORDER = {"error": 0, "warning": 1, "info": 2}


def _data_block(numbered: list[dict], resolved: list[dict]) -> str:
    """Hidden machine-readable block embedded at the end of the comment so the
    /omni-fix workflow can map finding IDs back to files/rules/prompts without
    hunting for artifacts. Base64-wrapped so finding text can't break the HTML
    comment. `head_sha` lets the fix command detect a stale (moved) branch.
    `resolved` is the cumulative ledger of findings fixed over the PR's life."""
    payload = {
        "head_sha": (os.environ.get("PR_HEAD_SHA") or "").strip(),
        "findings": numbered,
        "resolved": resolved,
    }
    b64 = base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return f"\n\n<!-- omni-bp-data:v1 {b64} -->"


def _sig(f: dict) -> tuple:
    """Stable identity for a finding across runs: (file, rule)."""
    return (f.get("file"), f.get("rule"))


def _compute_resolved(current: list[dict], prior: dict | None, diff: bool) -> list[dict]:
    """Cumulative 'resolved over this PR' ledger.

    diff=True  (we reviewed): newly_resolved = prior_open − current_open;
               resolved = (prior_resolved ∪ newly_resolved) − current_open
               (so a regression — a finding that reappears — drops off).
    diff=False (short-circuit, no review): carry the prior ledger forward
               unchanged so a no-op push neither wipes nor inflates it.
    """
    prior = prior or {}
    prior_resolved = prior.get("resolved") or []
    if not diff:
        return prior_resolved

    cur_sigs = {_sig(f) for f in current}
    head = (os.environ.get("PR_HEAD_SHA") or "").strip()

    ledger: dict[tuple, dict] = {}
    for r in prior_resolved:
        ledger[_sig(r)] = r
    for f in prior.get("findings") or []:
        if _sig(f) not in cur_sigs:  # was open, now gone -> resolved
            ledger.setdefault(_sig(f), {
                "file": f.get("file"), "rule": f.get("rule"), "resolved_sha": head,
            })
    # Drop anything that is currently open again (regression).
    return [r for sig, r in ledger.items() if sig not in cur_sigs]


def _resolved_capsule(resolved: list[dict]) -> list[str]:
    if not resolved:
        return []
    items = sorted(resolved, key=lambda r: (r.get("file") or "", r.get("rule") or ""))
    bullets = [f"- `{r.get('rule') or '?'}` · `{r.get('file') or '?'}`" for r in items]
    out = [f"**✅ Resolved over this PR ({len(items)}):**", ""]
    if len(items) <= 6:
        out += bullets
    else:
        out += ["<details><summary>show</summary>", ""] + bullets + ["", "</details>"]
    out.append("")
    return out


def _line_prefix(line, end_line) -> str:
    """Bold 'Line N:' / 'Lines N-M:' prefix for the Finding cell. '' if no line."""
    if not isinstance(line, int):
        return ""
    if isinstance(end_line, int) and end_line > line:
        return f"**Lines {line}-{end_line}:** "
    return f"**Line {line}:** "


def _md_cell(text: str | None) -> str:
    """Make text safe for a single markdown table cell: collapse newlines and
    escape pipes so they don't break the table."""
    s = (text or "").strip()
    s = re.sub(r"\s*\n\s*", " ", s)
    return s.replace("|", "\\|")

SKILLS_REPO = "exploreomni/omni-agent-skills"


def _provenance_footer() -> str:
    """Render a footer recording which omni-agent-skills revision we reviewed
    against. Driven by env vars set in the workflow; returns '' if absent."""
    version = (os.environ.get("SKILLS_VERSION") or "").strip()
    sha = (os.environ.get("SKILLS_SHA") or "").strip()
    short = (os.environ.get("SKILLS_SHORT") or "").strip() or sha[:7]
    if not version and not sha:
        return ""
    bits = []
    if version:
        bits.append(f"`omni-analytics` v{version}")
    if sha:
        url = f"https://github.com/{SKILLS_REPO}/tree/{sha}"
        bits.append(f"[`{short}`]({url})")
    return (
        f"\n\n---\n_Reviewed against the full Omni agent skills "
        f"({SKILLS_REPO}@main): {' · '.join(bits)}._"
    )


def _fmt_tokens(n) -> str:
    """Compact token count: 215243 -> '215.2k', 6919 -> '6.9k', 940 -> '940'."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "?"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _usage_footer(usage_path: str | None) -> str:
    """Render a cost/token telemetry line from the usage JSON written by
    extract-claude-json.py. Returns '' when unavailable."""
    if not usage_path or not Path(usage_path).exists():
        return ""
    try:
        u = json.loads(Path(usage_path).read_text())
    except (json.JSONDecodeError, OSError):
        return ""
    if not isinstance(u, dict) or u.get("total_cost_usd") is None:
        return ""

    cost = u.get("total_cost_usd")
    # Total input = fresh + cache-creation + cache-read (cache tokens are billed,
    # just at reduced rates — include them so the number reflects real volume).
    in_tot = sum(
        int(u.get(k) or 0)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    out_tot = u.get("output_tokens")
    turns = u.get("num_turns")
    dur_ms = u.get("duration_ms")

    bits = [f"${cost:.4f}".rstrip("0").rstrip(".")]
    bits.append(f"{_fmt_tokens(in_tot)} in / {_fmt_tokens(out_tot)} out")
    if turns is not None:
        bits.append(f"{turns} turns")
    if isinstance(dur_ms, (int, float)):
        bits.append(f"{dur_ms / 1000:.0f}s")
    return f"\n_Review cost: {' · '.join(bits)}._"


DEFAULT_TITLE = "Omni best-practices review"
DEFAULT_INTRO = (
    "_This check **fails on `error` findings**; warnings and info are advisory. "
    "Findings come from Claude reviewing the changed YAML against the full "
    "`omni-agent-skills` skill docs._"
)


def render(
    doc: dict,
    usage_path: str | None = None,
    prior: dict | None = None,
    diff_resolved: bool = False,
    title: str = DEFAULT_TITLE,
    intro: str = DEFAULT_INTRO,
    footer_note: str = "",
) -> str:
    findings = doc.get("findings") or []
    summary = (doc.get("summary") or "").strip()
    footer = _provenance_footer() + _usage_footer(usage_path)
    if footer_note.strip():
        footer += f"\n\n---\n_{footer_note.strip()}_"
    resolved = _compute_resolved(findings, prior, diff_resolved)
    capsule = _resolved_capsule(resolved)

    lines: list[str] = []
    lines.append(f"## {title}")
    lines.append("")
    lines.append(intro)
    lines.append("")

    if not findings:
        lines.append("✅ No best-practices issues found in the changed YAML.")
        if summary:
            lines.append("")
            lines.append(f"_{summary}_")
        if capsule:
            lines.append("")
            lines.extend(capsule)
        return "\n".join(lines).rstrip() + footer + _data_block([], resolved) + "\n"

    counts = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        counts[f.get("severity", "info")] = counts.get(f.get("severity", "info"), 0) + 1
    summary_bits = []
    if counts["error"]:
        summary_bits.append(f"{counts['error']} error(s)")
    if counts["warning"]:
        summary_bits.append(f"{counts['warning']} warning(s)")
    if counts["info"]:
        summary_bits.append(f"{counts['info']} info")
    lines.append(f"**Findings:** {', '.join(summary_bits)}")
    if summary:
        lines.append("")
        lines.append(f"_{summary}_")
    lines.append("")
    if capsule:
        lines.extend(capsule)

    # Group by file, preserving severity ordering within each file.
    # Two-level IDs: <file#>.<finding#> (file# = sorted file order).
    by_file: dict[str, list[dict]] = {}
    for f in findings:
        by_file.setdefault(f.get("file") or "(unknown file)", []).append(f)

    numbered: list[dict] = []
    for file_idx, path in enumerate(sorted(by_file), start=1):
        items = sorted(by_file[path], key=lambda i: SEV_ORDER.get(i.get("severity", "info"), 3))
        lines.append(f"### {file_idx}. `{path}`")
        lines.append("")
        lines.append("| # | Severity | Rule | Finding | Fix prompt |")
        lines.append("|:--|:--:|:--|:--|:--|")
        for finding_idx, it in enumerate(items, start=1):
            fid = f"{file_idx}.{finding_idx}"
            emoji = SEV_EMOJI.get(it.get("severity", "info"), "•")
            rule = it.get("rule") or "?"
            prefix = _line_prefix(it.get("line"), it.get("end_line"))
            msg = _md_cell(it.get("message"))
            finding = f"{prefix}{msg}" if prefix else msg
            sug = _md_cell(it.get("suggestion")) or "—"
            lines.append(f"| **{fid}** | {emoji} | `{rule}` | {finding} | {sug} |")
            numbered.append({
                "id": fid,
                "file": path,
                "line": it.get("line"),
                "end_line": it.get("end_line"),
                "severity": it.get("severity"),
                "rule": it.get("rule"),
                "message": it.get("message"),
                "suggestion": it.get("suggestion"),
            })
        lines.append("")

    return "\n".join(lines).rstrip() + footer + _data_block(numbered, resolved) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, help="Path to findings JSON")
    ap.add_argument("--out", required=True, help="Path to write markdown body")
    ap.add_argument(
        "--usage",
        default="",
        help="Optional path to cost/usage telemetry JSON (from extract-claude-json.py).",
    )
    ap.add_argument(
        "--prior",
        default="",
        help="Optional path to the prior comment's decoded data block (extract-bp-prior.py).",
    )
    ap.add_argument(
        "--diff-resolved",
        action="store_true",
        help="Compute newly-resolved findings by diffing prior vs current "
             "(only when we actually reviewed; off on the short-circuit path).",
    )
    ap.add_argument("--title", default=DEFAULT_TITLE,
                    help="Comment heading (per-reviewer).")
    ap.add_argument("--intro", default=DEFAULT_INTRO,
                    help="Intro line under the heading (per-reviewer).")
    ap.add_argument("--footer-note", default="",
                    help="Extra italicized footer line (e.g. agent-job provenance).")
    args = ap.parse_args()

    raw = Path(args.inp).read_text() if Path(args.inp).exists() else args.inp
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        # Tolerate Claude wrapping JSON in code fences
        s = raw.strip()
        if s.startswith("```"):
            s = "\n".join(s.split("\n")[1:-1])
        doc = json.loads(s)

    prior = None
    if args.prior and Path(args.prior).exists():
        try:
            prior = json.loads(Path(args.prior).read_text())
        except (json.JSONDecodeError, OSError):
            prior = None

    body = render(doc, usage_path=args.usage, prior=prior,
                  diff_resolved=args.diff_resolved, title=args.title,
                  intro=args.intro, footer_note=args.footer_note)
    Path(args.out).write_text(body)
    print(body, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
