#!/usr/bin/env python3
"""Build the generation input for /omni-fix.

Given the selected findings, group them by file, fetch each file's CURRENT YAML
from the Omni branch (`omni models yaml-get`), and emit fix-input.json:

    {
      "branch_id": "...",
      "files": [
        { "git_path": "omni/<model>/ECOMM/foo.view.yaml",
          "omni_filename": "ECOMM/foo.view",
          "current_yaml": "...",
          "findings": [ {id, rule, message, suggestion, line, end_line}, ... ] }
      ]
    }

No model mutation here — yaml-get is read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


# Directory the Omni git integration writes model YAML into. Omni's default
# modelPath is `<MODEL_DIR>/<model name>`, so the omni filename is the path
# relative to `<MODEL_DIR>/<model name>/`.
MODEL_DIR = os.environ.get("OMNI_MODEL_DIR", "omni").strip("/")


def git_path_to_omni_filename(path: str) -> str:
    """<MODEL_DIR>/<model dir>/ECOMM/foo.view.yaml  ->  ECOMM/foo.view"""
    prefix = MODEL_DIR + "/"
    rel = path
    if path.startswith(prefix):
        rel = path[len(prefix):]
        rel = rel.split("/", 1)[1] if "/" in rel else rel
    return rel[:-5] if rel.endswith(".yaml") else rel


def yaml_get(model_id: str, branch_id: str, omni_filename: str) -> str | None:
    cmd = [
        "omni", "models", "yaml-get", model_id,
        "--filename", omni_filename, "--branchid", branch_id,
        "--compact", "-o", "json",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        print(f"::warning::yaml-get failed for {omni_filename}: {e.stderr[:300]}", file=sys.stderr)
        return None
    try:
        files = json.loads(out).get("files") or {}
    except json.JSONDecodeError:
        return None
    # yaml-get keys by the full path; match exactly or by suffix.
    if omni_filename in files:
        return files[omni_filename]
    for k, v in files.items():
        if k.endswith(omni_filename):
            return v
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selected", required=True)
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--branch-id", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    selected = json.loads(Path(args.selected).read_text()).get("selected") or []

    by_file: dict[str, list[dict]] = {}
    for f in selected:
        by_file.setdefault(f["file"], []).append(f)

    files_out = []
    missing = []
    for git_path, findings in by_file.items():
        omni_name = git_path_to_omni_filename(git_path)
        current = yaml_get(args.model_id, args.branch_id, omni_name)
        if current is None:
            missing.append(git_path)
            continue
        files_out.append({
            "git_path": git_path,
            "omni_filename": omni_name,
            "current_yaml": current,
            "findings": [
                {k: f.get(k) for k in ("id", "rule", "message", "suggestion", "line", "end_line")}
                for f in findings
            ],
        })

    Path(args.out).write_text(json.dumps({"branch_id": args.branch_id, "files": files_out}, indent=2))
    if missing:
        print(f"::warning::Could not fetch YAML for: {missing}", file=sys.stderr)
    if not files_out:
        print("::error::No files could be fetched for the selected findings.", file=sys.stderr)
        return 6
    print(f"Prepared {len(files_out)} file(s) for fixing.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
