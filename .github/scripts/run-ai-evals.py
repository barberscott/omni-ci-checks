#!/usr/bin/env python3
"""Run an Omni AI-eval prompt set and emit findings for diff-findings.py.

Built on Omni's first-class Eval Runs API. A prompt set lives on the model
(referenced by id); each prompt carries a natural-language `expectation` that
an analysis judge scores the agent's answer against (0-1). `runs-create`
accepts `run_config.branch_id`, so the run executes against the PR's Omni
branch.

This script runs the prompt set ONCE (against the given branch, or base when no
branch id) and writes the {issues, summary} shape the
diff-findings.py / format-findings.py / validation-summary pipeline
consumes. The CI job runs it twice (branch + base) and diffs for net-new
-- a prompt that fails on the branch but not base is a regression and blocks.

A prompt is a FAILURE when its judge score is below OMNI_EVAL_THRESHOLD
(default 0.8), or when the agentic job errored / returned no score.

Usage:
    run-ai-evals.py <prompt_set_id> [--branch-id <uuid>] --out branch.json

Env:
    OMNI_PROFILE_NAME    optional CLI profile
    OMNI_EVAL_THRESHOLD  judge-score pass threshold (default 0.8)
    OMNI_EVAL_TIMEOUT    seconds to wait for the run (default 600)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

THRESHOLD = float(os.environ.get("OMNI_EVAL_THRESHOLD", "0.8"))
TIMEOUT = int(os.environ.get("OMNI_EVAL_TIMEOUT", "600"))


def _key(*parts) -> str:
    raw = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _omni(*args: str, profile: str | None):
    cmd = ["omni"] + (["-p", profile] if profile else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def _scores_pending(rows: list) -> bool:
    # The run flips to COMPLETE a beat before the judge writes scores; wait for
    # every answered prompt to be scored so we don't read a spurious null.
    for r in rows:
        job_state = (r.get("agentic_job") or {}).get("state")
        if job_state in ("COMPLETE", None) and r.get("score") is None and not r.get("error_reason"):
            return True
    return False


def _write(out: str | None, payload: dict) -> None:
    text = json.dumps(payload, indent=2, default=str)
    if out:
        Path(out).write_text(text)
    else:
        sys.stdout.write(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt_set_id")
    ap.add_argument("--branch-id", default=None)
    ap.add_argument("--profile", default=os.environ.get("OMNI_PROFILE_NAME"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    where = "branch" if args.branch_id else "base"
    body: dict = {"prompt_set_id": args.prompt_set_id, "description": f"CI eval run ({where})"}
    if args.branch_id:
        body["run_config"] = {"branch_id": args.branch_id}

    rc, out, err = _omni("ai-eval", "runs-create", "--body", json.dumps(body),
                         "--compact", "-o", "json", profile=args.profile)
    if rc != 0:
        msg = (err.strip() or out.strip())[:300]
        print(f"::error::eval runs-create failed ({where}): {msg}", file=sys.stderr)
        issue = {"key": _key("ai_eval_infra", where), "severity": "error",
                 "rule": "ai_eval_infra", "location": f"ai-evals ({where})",
                 "message": f"eval run could not start: {msg}", "raw": {}}
        _write(args.out, {"issues": [issue], "summary": {"total": 0, "passed": 0, "failed": 1, "where": where}})
        return 0

    run = json.loads(out).get("run") or {}
    run_id = run.get("id")
    final = run

    deadline = time.time() + TIMEOUT
    delay = 5.0
    while time.time() < deadline:
        rc, out, err = _omni("ai-eval", "runs-get", run_id, "--compact", "-o", "json", profile=args.profile)
        if rc == 0:
            final = json.loads(out).get("run") or {}
            status = final.get("status")
            terminal = status not in ("RUNNING", "QUEUED", None)
            if terminal and not _scores_pending(final.get("results", []) or []):
                break
        time.sleep(delay)
        delay = min(delay * 1.3, 15.0)

    rows = final.get("results", []) or []
    issues = []
    passed = 0
    for r in rows:
        score = r.get("score")
        prompt = r.get("prompt") or ""
        if isinstance(score, (int, float)) and score >= THRESHOLD:
            passed += 1
            continue
        if r.get("error_reason"):
            reason = f"agent error: {r['error_reason']}"
        elif score is None:
            reason = "no judge score returned"
        else:
            reason = f"judge score {score:.2f} < {THRESHOLD:.2f}"
        issues.append({
            "key": _key(prompt),  # stable across base/branch runs -> net-new diffing
            "severity": "error",
            "rule": "ai_eval_failure",
            "location": prompt[:80],
            "message": f"{reason} (expected: {(r.get('expectation') or '')[:120]})",
            "raw": {
                "score": score,
                "run_id": run_id,
                "where": where,
                "conversation_id": (r.get("agentic_job") or {}).get("conversation_id"),
            },
        })

    summary = {
        "total": len(rows),
        "passed": passed,
        "failed": len(issues),
        "run_id": run_id,
        "status": final.get("status"),
        "where": where,
        "threshold": THRESHOLD,
        "prompt_set_id": args.prompt_set_id,
    }
    _write(args.out, {"issues": issues, "summary": summary})
    print(f"AI evals ({where}): {passed}/{len(rows)} >= {THRESHOLD} (status {final.get('status')})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
