#!/usr/bin/env python3
"""Shared-model hygiene check: no hand-authored base views in the shared model.

A table-backed base view must come from the **schema layer** (it appears in
combined/resolved mode but NOT in the extension/staged layer). If a changed
view shows a top-level `table_name:` in **extension mode**, it was authored by
hand in the shared model — a violation. The fix is to add fields to the
schema-generated view for that table instead.

Detection is provenance-accurate (unlike inspecting the git file, where
schema-origin and hand-authored views both carry `table_name:`): we ask Omni
for each changed view in `--mode extension` and flag a top-level `table_name:`.

Emits a diff-findings-shaped JSON (so it slots into the combined validation
comment). Always exits 0; the workflow's fail step blocks on errors.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TOP_TABLE_NAME = re.compile(r"(?m)^table_name:\s*\S")



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


def extension_yaml(model_id: str, branch_id: str, omni_filename: str) -> str | None:
    cmd = [
        "omni", "models", "yaml-get", model_id,
        "--filename", omni_filename, "--branchid", branch_id,
        "--mode", "extension", "--compact", "-o", "json",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        files = json.loads(out).get("files") or {}
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"::warning::extension yaml-get failed for {omni_filename}: {e}", file=sys.stderr)
        return None
    if omni_filename in files:
        return files[omni_filename]
    for k, v in files.items():
        if k.endswith(omni_filename):
            return v
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--branch-id", required=True)
    ap.add_argument("--files", required=True, help="File with newline-separated changed view paths")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    paths = [p.strip() for p in Path(args.files).read_text().splitlines() if p.strip()]
    views = [p for p in paths if p.endswith(".view.yaml")]

    errors = []
    for git_path in views:
        omni_name = git_path_to_omni_filename(git_path)
        ext = extension_yaml(args.model_id, args.branch_id, omni_name)
        if ext is None:
            continue
        if TOP_TABLE_NAME.search(ext):
            line = next((i for i, l in enumerate(ext.splitlines(), 1)
                         if l.startswith("table_name:")), 1)
            errors.append({
                "key": f"shared-model-base-view::{git_path}",
                "severity": "error",
                "rule": "shared-model-base-view",
                "location": git_path,
                "line": line,
                "message": (
                    "Hand-authored base view: `table_name:` is present in the "
                    "extension layer. Table-backed base views must come from the "
                    "schema model, not the shared/staged model. Add your fields "
                    "to the schema-generated view for this table instead."
                ),
            })
            print(f"VIOLATION: {git_path} declares table_name in extension mode", file=sys.stderr)
        else:
            print(f"ok: {git_path}", file=sys.stderr)

    result = {
        "kind": "shared-model-hygiene",
        "base_total": 0,
        "branch_total": len(errors),
        "net_new": errors,
        "fixed": [],
        "errors": errors,
        "warnings": [],
        "infos": [],
    }
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(f"{len(errors)} shared-model hygiene violation(s).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
