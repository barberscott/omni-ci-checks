#!/usr/bin/env python3
"""Best-practices review of changed model YAML by Omni's own modeling agent.

Submits a READ-ONLY review prompt to the Agentic Jobs API (`omni ai
job-submit`) against the PR's Omni branch, polls to completion, and parses the
agent's JSON reply into the same {summary, findings} shape the Claude-based
best-practices job produces — so the comment/annotation/fix pipeline is shared.

The agent reviews the RESOLVED model state on the branch (schema-aware, and
with any standards in the model's own `ai_context` applied automatically), not
the git diff text — so findings carry no line numbers. Company standards from
the repo are inlined into the prompt verbatim.

Read-only is enforced two ways: the prompt forbids writes, and this script
snapshots the branch's staged YAML before and after the job and exits non-zero
if anything changed (the job then fails hard — a mutated PR branch must never
pass silently).

Usage:
    run-agent-review.py <model-id> --branch-id <uuid> \
        --files changed.txt \
        --standards .github/best-practices/omni-models.md \
        --prompt-head .github/prompts/agent-review.md \
        --out findings.json --meta-out meta.json

Exit codes: 0 = review completed (findings gate is the workflow's job);
2 = the agent modified branch YAML (safety violation); 1 = infra failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Directory the Omni git integration writes model YAML into. Omni's default
# modelPath is `<MODEL_DIR>/<model name>`, so the omni filename is the path
# relative to `<MODEL_DIR>/<model name>/`.
MODEL_DIR = os.environ.get("OMNI_MODEL_DIR", "omni").strip("/")

POLL_SECONDS = 10
VALID_SEVERITIES = {"error", "warning", "info"}


def git_path_to_omni_filename(path: str) -> str:
    """<MODEL_DIR>/<model dir>/ECOMM/foo.view.yaml  ->  ECOMM/foo.view"""
    prefix = MODEL_DIR + "/"
    rel = path
    if path.startswith(prefix):
        rel = path[len(prefix):]
        rel = rel.split("/", 1)[1] if "/" in rel else rel
    return rel[:-5] if rel.endswith(".yaml") else rel


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
    """Drop fenced code blocks from the standards file before inlining it.
    The standards doc keeps its inactive example rules inside a fence
    ("copy an entry out of the fence to activate it"); stripping them here
    guarantees the agent can never emit findings for example rules — observed
    to happen when the fence was left in."""
    return FENCED_BLOCK.sub("(example block omitted)", markdown)


def build_prompt(head: str, standards: str, mapping: dict[str, str]) -> str:
    standards = strip_fenced_blocks(standards)
    parts = [head.rstrip(), "", "## Company standards", "", standards.rstrip(), ""]
    parts += ["## Files under review", ""]
    for omni_name in mapping:
        parts.append(f"- {omni_name}")
    parts += ["",
              "Remember: your entire final response is the JSON object only — "
              "no fences, no prose."]
    return "\n".join(parts)


def normalize_findings(doc: dict, mapping: dict[str, str]) -> dict:
    """Validate shape, clamp severities, translate omni filenames back to the
    git paths GitHub annotations need."""
    findings = []
    for f in doc.get("findings") or []:
        if not isinstance(f, dict) or not f.get("message"):
            continue
        sev = f.get("severity")
        if sev not in VALID_SEVERITIES:
            sev = "info"
        omni_name = (f.get("file") or "").strip()
        findings.append({
            "file": mapping.get(omni_name, omni_name),
            "field": f.get("field"),
            "rule": (f.get("rule") or "unlabeled").strip(),
            "severity": sev,
            "message": str(f.get("message")).strip(),
            "suggestion": (str(f.get("suggestion")).strip()
                           if f.get("suggestion") else None),
        })
    return {"summary": (doc.get("summary") or "").strip(), "findings": findings}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model_id")
    ap.add_argument("--branch-id", required=True)
    ap.add_argument("--files", required=True,
                    help="File with newline-separated changed git paths")
    ap.add_argument("--standards", required=True)
    ap.add_argument("--prompt-head", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--meta-out", default="")
    ap.add_argument("--timeout", type=int, default=600,
                    help="Max seconds to wait for the job")
    args = ap.parse_args()

    git_paths = [p.strip() for p in Path(args.files).read_text().splitlines() if p.strip()]
    mapping = {git_path_to_omni_filename(p): p for p in git_paths}
    if not mapping:
        Path(args.out).write_text(json.dumps(
            {"summary": "No model YAML changed in this PR; skipping review.",
             "findings": []}))
        return 0

    prompt = build_prompt(
        Path(args.prompt_head).read_text(),
        Path(args.standards).read_text(),
        mapping,
    )
    print(f"Reviewing {len(mapping)} file(s) via the Omni agent:", file=sys.stderr)
    for name in mapping:
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

    # Write-guard: the review must not have touched the branch.
    guard_after = staged_fingerprint(args.branch_id)
    if guard_after != guard_before:
        print(f"::error::Omni agent job {job_id} MODIFIED the branch's staged "
              "YAML during a read-only review. Inspect the branch before "
              "trusting this PR's Omni state.", file=sys.stderr)
        return 2

    metrics = result.get("metrics") or {}
    tools = metrics.get("toolBreakdown") or {}
    print(f"Tool usage: {json.dumps(tools)}", file=sys.stderr)

    doc = normalize_findings(parse_agent_json(result.get("resultSummary")), mapping)
    Path(args.out).write_text(json.dumps(doc, indent=2))
    sevs = [f["severity"] for f in doc["findings"]]
    print(f"{len(sevs)} finding(s): {sevs.count('error')} error, "
          f"{sevs.count('warning')} warning, {sevs.count('info')} info",
          file=sys.stderr)

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
