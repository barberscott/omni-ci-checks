#!/usr/bin/env python3
"""Generate corrected YAML for /omni-fix using Omni's own modeling agent.

The Omni-native counterpart of the Claude generation step: submits a
generate-only prompt (the selected findings plus each file's current branch
YAML from fix-input.json) to the Agentic Jobs API (`omni ai job-submit`),
polls to completion, and writes the agent's JSON reply — the same
{files, delete, skipped} contract the Claude step produces — for
apply-omni-fix.py to consume unchanged.

Generation is read-only: the prompt forbids writes, and this script snapshots
the branch's staged YAML before and after the job and exits non-zero if
anything changed (a mutated branch must never pass silently). All writes
happen later, deterministically, in apply-omni-fix.py.

Usage:
    run-agent-fix.py <model-id> --branch-id <uuid> \
        --fix-input fix-input.json \
        --standards .github/best-practices/omni-models.md \
        --prompt-head .github/prompts/omni-fix-agent.md \
        --out agent-result.json --meta-out agent-meta.json

Exit codes: 0 = generation completed (apply decides what to do with it);
2 = the agent modified branch YAML (safety violation); 1 = infra failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

POLL_SECONDS = 10


def omni(args: list[str]) -> dict:
    out = subprocess.run(["omni", *args, "--compact", "-o", "json"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"omni {' '.join(args[:3])}... failed: {out.stderr.strip()[:500]}")
    return json.loads(out.stdout)


def staged_fingerprint(branch_id: str) -> str:
    """Hash of the branch's staged YAML — the write-guard baseline."""
    files = omni(["models", "yaml-get", branch_id, "--mode", "staged"]).get("files") or {}
    blob = json.dumps(files, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def parse_agent_json(summary: str) -> dict:
    """resultSummary should be raw JSON per the prompt contract; tolerate
    code fences or surrounding prose by extracting the first balanced object."""
    s = (summary or "").strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    if s.startswith("```"):
        s = "\n".join(s.split("\n")[1:-1]).strip()
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = s.find("{", start + 1)
    raise ValueError(f"No parseable JSON object in agent reply: {s[:300]!r}")


FENCED_BLOCK = re.compile(r"^```.*?^```\s*$", re.MULTILINE | re.DOTALL)


def strip_fenced_blocks(markdown: str) -> str:
    """Drop fenced code blocks from the standards file before inlining it
    (the standards doc keeps inactive example rules inside a fence)."""
    return FENCED_BLOCK.sub("(example block omitted)", markdown)


def build_prompt(head: str, standards: str, fix_input_text: str) -> str:
    parts = [head.rstrip(), "",
             "## Company standards", "",
             strip_fenced_blocks(standards).rstrip(), "",
             "## fix-input.json (the files + the findings to fix)", "",
             fix_input_text.strip(), "",
             "Remember: your entire final response is the JSON object only — "
             "no fences, no prose."]
    return "\n".join(parts)


def normalize_result(doc: dict, allowed: set[str]) -> dict:
    """Shape-check the agent reply. Entries for files outside fix-input are
    dropped here (apply-omni-fix.py filters again — belt and suspenders)."""
    files = []
    for f in doc.get("files") or []:
        if not isinstance(f, dict):
            continue
        fn = (f.get("omni_filename") or "").strip()
        if fn in allowed and isinstance(f.get("yaml"), str) and f["yaml"].strip():
            files.append({"omni_filename": fn, "yaml": f["yaml"]})
        elif fn:
            print(f"::warning::agent proposed a change outside fix-input, dropped: {fn}",
                  file=sys.stderr)
    delete = [fn for fn in (doc.get("delete") or [])
              if isinstance(fn, str) and fn.strip() in allowed]
    skipped = [s for s in (doc.get("skipped") or []) if isinstance(s, dict)]
    return {"files": files, "delete": delete, "skipped": skipped}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model_id")
    ap.add_argument("--branch-id", required=True)
    ap.add_argument("--fix-input", required=True)
    ap.add_argument("--standards", required=True)
    ap.add_argument("--prompt-head", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--meta-out", default="")
    ap.add_argument("--timeout", type=int, default=900,
                    help="Max seconds to wait for the job")
    args = ap.parse_args()

    fix_input_text = Path(args.fix_input).read_text()
    fix_input = json.loads(fix_input_text)
    allowed = {f["omni_filename"] for f in fix_input.get("files", [])}
    if not allowed:
        print("::error::fix-input.json contains no files.", file=sys.stderr)
        return 1

    prompt = build_prompt(Path(args.prompt_head).read_text(),
                          Path(args.standards).read_text(),
                          fix_input_text)
    print(f"Generating fixes for {len(allowed)} file(s) via the Omni agent:",
          file=sys.stderr)
    for name in sorted(allowed):
        print(f"  - {name}", file=sys.stderr)

    guard_before = staged_fingerprint(args.branch_id)

    body = {"modelId": args.model_id, "branchId": args.branch_id, "prompt": prompt}
    job = omni(["ai", "job-submit", "--body", json.dumps(body)])
    job_id = job.get("jobId")
    if not job_id:
        raise RuntimeError(f"job-submit returned no jobId: {job}")
    print(f"Submitted agent job {job_id}", file=sys.stderr)

    deadline = time.time() + args.timeout
    state = None
    while time.time() < deadline:
        state = omni(["ai", "job-status", job_id]).get("state")
        print(f"  state: {state}", file=sys.stderr)
        if state == "COMPLETE":
            break
        if state not in ("QUEUED", "EXECUTING"):
            raise RuntimeError(f"Agent job {job_id} ended in state {state}")
        time.sleep(POLL_SECONDS)
    if state != "COMPLETE":
        subprocess.run(["omni", "ai", "job-cancel", job_id],
                       capture_output=True, text=True)
        raise RuntimeError(f"Agent job {job_id} timed out after {args.timeout}s")

    result = omni(["ai", "job-result", job_id])

    # Write-guard: generation must not have touched the branch.
    guard_after = staged_fingerprint(args.branch_id)
    if guard_after != guard_before:
        print(f"::error::Omni agent job {job_id} MODIFIED the branch's staged "
              "YAML during generate-only fix drafting. Inspect the branch; "
              "nothing will be applied.", file=sys.stderr)
        return 2

    metrics = result.get("metrics") or {}
    print(f"Tool usage: {json.dumps(metrics.get('toolBreakdown') or {})}",
          file=sys.stderr)

    doc = normalize_result(parse_agent_json(result.get("resultSummary")), allowed)
    Path(args.out).write_text(json.dumps(doc, indent=2))
    print(f"{len(doc['files'])} file(s) rewritten, {len(doc['delete'])} delete(s), "
          f"{len(doc['skipped'])} skipped", file=sys.stderr)

    if args.meta_out:
        Path(args.meta_out).write_text(json.dumps({
            "job_id": job_id,
            "duration_ms": metrics.get("durationMs"),
            "tool_calls": metrics.get("toolCallCount"),
            "chat_url": result.get("omniChatUrl"),
        }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, json.JSONDecodeError) as e:
        print(f"::error::{e}", file=sys.stderr)
        sys.exit(1)
