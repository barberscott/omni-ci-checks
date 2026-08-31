#!/usr/bin/env python3
"""Apply /omni-fix corrected YAML to the Omni branch, validate, commit.

Takes Claude's generation output (corrected YAML per file) and:
  1. snapshots branch validation issues (pre),
  2. writes each corrected file via `omni models yaml-create` (mode extension),
  3. re-validates; if any NET-NEW issue appears, ROLLS BACK every file to its
     original YAML and aborts (nothing commits),
  4. otherwise `omni models commit` -> one new commit on the PR branch.

Writes a markdown summary for the PR comment. Exit codes:
  0  applied + committed
  6  no corrected files parsed from Claude output
  8  validation regressed -> rolled back, nothing committed
  9  commit failed
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str]) -> tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def _extract_result(execution_file: str, structured: str) -> dict | None:
    for raw in (structured, None):
        if raw:
            d = _parse(raw)
            if d:
                return d
    try:
        msgs = json.loads(Path(execution_file).read_text())
    except Exception:
        return None
    if isinstance(msgs, list):
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("type") == "result" and m.get("subtype") == "success":
                d = _parse(m.get("result") or "")
                if d:
                    return d
    return None


def _parse(text: str) -> dict | None:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    def _ok(d):
        return isinstance(d, dict) and ("files" in d or "delete" in d)
    try:
        d = json.loads(s)
        return d if _ok(d) else None
    except json.JSONDecodeError:
        for m in sorted(re.findall(r"\{(?:[^{}]|\{[^{}]*\})*\}", s, re.DOTALL), key=len, reverse=True):
            try:
                d = json.loads(m)
                if _ok(d):
                    return d
            except json.JSONDecodeError:
                continue
    return None


def _validate_issues(model_id: str, branch_id: str) -> dict[str, dict]:
    """Map a stable key -> validation issue dict for the branch."""
    rc, out, _ = _run([
        "omni", "models", "validate", model_id, "--branchid", branch_id, "--compact", "-o", "json"
    ])
    try:
        issues = json.loads(out)
    except json.JSONDecodeError:
        issues = []
    if not isinstance(issues, list):
        issues = []
    return {json.dumps(i, sort_keys=True): i for i in issues}


def _write(model_id: str, branch_id: str, filename: str, yaml_text: str, msg: str) -> bool:
    body = json.dumps({
        "fileName": filename, "yaml": yaml_text, "mode": "extension",
        "branchId": branch_id, "commitMessage": msg,
    })
    rc, out, err = _run([
        "omni", "models", "yaml-create", model_id, "--body", body, "--compact", "-o", "json"
    ])
    if rc != 0:
        print(f"::warning::yaml-create failed for {filename}: {err[:300]}", file=sys.stderr)
    return rc == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execution-file", default="")
    ap.add_argument("--structured-output", default="")
    ap.add_argument("--result-json", default="",
                    help="Path to a ready {files,delete,skipped} JSON document "
                         "(the Omni-agent engine); takes precedence over the "
                         "Claude execution outputs when the file exists")
    ap.add_argument("--fix-input", required=True)
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--branch-id", required=True)
    ap.add_argument("--summary-md", required=True)
    args = ap.parse_args()

    fix_input = json.loads(Path(args.fix_input).read_text())
    originals = {f["omni_filename"]: f["current_yaml"] for f in fix_input.get("files", [])}
    # map omni_filename -> the finding ids targeted (for the summary)
    ids_for = {f["omni_filename"]: [x.get("id") for x in f.get("findings", [])] for f in fix_input.get("files", [])}

    result = None
    if args.result_json and Path(args.result_json).is_file():
        try:
            candidate = json.loads(Path(args.result_json).read_text())
            if isinstance(candidate, dict) and ("files" in candidate or "delete" in candidate):
                result = candidate
        except (json.JSONDecodeError, OSError):
            result = None
    if result is None:
        result = _extract_result(args.execution_file, args.structured_output)
    out = Path(args.summary_md)
    if not result or (not result.get("files") and not result.get("delete")):
        out.write_text("## /omni-fix\n\n❌ Could not parse corrected YAML from the generation step. Nothing applied.\n")
        return 6

    changed = [f for f in (result.get("files") or []) if f.get("omni_filename") in originals and f.get("yaml")]
    # Deletes: omni_filenames to remove. Only allow ones we have originals for
    # (so we can roll back), and don't delete a file we're also rewriting.
    to_delete = [
        fn for fn in (result.get("delete") or [])
        if fn in originals and fn not in {f["omni_filename"] for f in changed}
    ]
    skipped = result.get("skipped") or []
    if not changed and not to_delete:
        out.write_text("## /omni-fix\n\n⚠️ The generation step returned no applicable file changes. Nothing applied.\n")
        return 6

    pre = _validate_issues(args.model_id, args.branch_id)

    edited, deleted = [], []
    for f in changed:
        fn = f["omni_filename"]
        if _write(args.model_id, args.branch_id, fn, f["yaml"], "omni-fix: apply selected best-practice fixes"):
            edited.append(fn)
    # Delete via the YAML endpoint (empty yaml) — NOT delete-view, which is
    # not supported on git-linked shared models.
    for fn in to_delete:
        if _write(args.model_id, args.branch_id, fn, "", "omni-fix: remove view per best-practice fix"):
            deleted.append(fn)
    touched = edited + deleted

    post = _validate_issues(args.model_id, args.branch_id)
    new_keys = set(post) - set(pre)
    # Only ERROR-severity net-new issues abort. A delete can leave a benign,
    # non-blocking "references a table that does not exist" warning until the
    # commit re-syncs; that shouldn't trigger a rollback.
    new_errors = [post[k] for k in new_keys if not post[k].get("is_warning")]
    if new_errors:
        for fn in touched:  # restore originals (re-creates deleted files too)
            _write(args.model_id, args.branch_id, fn, originals[fn], "omni-fix: rollback (validation regressed)")
        lines = ["## /omni-fix", "", f"❌ Aborted — applying the fix introduced {len(new_errors)} new validation **error(s)**. Rolled back; nothing committed.", "", "<details><summary>New errors</summary>", ""]
        for obj in new_errors[:20]:
            lines.append(f"- `{obj.get('location','?')}` — {str(obj.get('message',''))[:200]}")
        lines += ["", "</details>"]
        out.write_text("\n".join(lines) + "\n")
        return 8

    # commit
    rc, cout, cerr = _run([
        "omni", "models", "commit", args.model_id, "--body",
        json.dumps({"branch_id": args.branch_id, "commit_message": "omni-fix: apply selected best-practice fixes"}),
        "--compact", "-o", "json",
    ])
    if rc != 0:
        out.write_text(f"## /omni-fix\n\n❌ Files updated on the branch but `omni models commit` failed:\n\n```\n{cerr[:500]}\n```\n")
        return 9
    try:
        commit = json.loads(cout)
    except json.JSONDecodeError:
        commit = {}

    lines = ["## /omni-fix", ""]
    n = len(edited) + len(deleted)
    lines.append(f"✅ Applied fixes and committed to the branch ({n} file(s): {len(edited)} edited, {len(deleted)} deleted).")
    lines.append("")
    for fn in edited:
        ids = ", ".join(i for i in ids_for.get(fn, []) if i)
        lines.append(f"- `{fn}` — fixed {ids}")
    for fn in deleted:
        ids = ", ".join(i for i in ids_for.get(fn, []) if i)
        lines.append(f"- `{fn}` — **deleted** ({ids})")
    if skipped:
        lines.append("")
        lines.append("**Skipped:**")
        for s in skipped:
            lines.append(f"- {s.get('id','?')}: {s.get('reason','')}")
    sha = commit.get("git_sha")
    if sha:
        lines.append("")
        lines.append(f"_Commit `{sha[:7]}` pushed via Omni; checks will re-run._")
    out.write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
