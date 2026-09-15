#!/usr/bin/env python3
"""Read Omni model identity from OmniFlow's `.omni/flow.json`.

This suite shares OmniFlow's trusted bootstrap metadata instead of keeping its
own copy in repository variables. The file shape is OmniFlow's:

    {"version": 1,
     "models": [{"base_url": "https://customer.omniapp.co",
                 "model_id": "<shared model uuid>",
                 "model_path": "omni/my_model",
                 "base_branch": "main", ...}]}

Usage:
    read-flow-config.py [--ref origin/main] [--base-ref origin/main]
                        [--path .omni/flow.json]

--ref       Read the file from that git ref (`git show <ref>:<path>`) instead
            of the working tree. On pull_request events pass the BASE ref so a
            PR cannot redirect the token by editing the file (OmniFlow's
            trusted-base rule).
--base-ref  Also diff `<base-ref>...HEAD` under each model_path and select the
            model whose files changed. Emits `model=true|false`.

Model selection: one registered model is selected unconditionally; with
several, the one whose model_path contains the PR's changed files is chosen,
and changes spanning more than one model are an error.

Emits `key=value` lines to $GITHUB_OUTPUT when set, else to stdout:
    base_url, model_id, model_path, model_dir, base_branch, model[=true|false]

`model_dir` is the parent of model_path (Omni's default modelPath is
`omni/<model name>`); the other scripts map repo paths to Omni filenames as
`<model_dir>/<model name>/<file>`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import PurePosixPath

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def die(msg: str) -> "NoReturn":  # noqa: F821
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(2)


def load(ref: str | None, path: str) -> dict:
    try:
        if ref:
            raw = subprocess.check_output(["git", "show", f"{ref}:{path}"], text=True)
        else:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
    except (subprocess.CalledProcessError, OSError) as exc:
        die(f"Cannot read {path}" + (f" from {ref}" if ref else "") + f": {exc}. "
            "Create it from OmniFlow's .omni/flow.example.json.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        die(f"{path} is not valid JSON: {exc}")


def validate(doc: dict, path: str) -> list[dict]:
    if not isinstance(doc, dict) or doc.get("version") != 1:
        die(f"{path}: expected an object with \"version\": 1")
    models = doc.get("models")
    if not isinstance(models, list) or not models:
        die(f"{path}: \"models\" must be a non-empty list")
    seen_paths: set[str] = set()
    for i, m in enumerate(models):
        if not isinstance(m, dict):
            die(f"{path}: models[{i}] must be an object")
        url = str(m.get("base_url", ""))
        if not url.startswith("https://") or "/" in url[len("https://"):].rstrip("/"):
            die(f"{path}: models[{i}].base_url must be an https origin, e.g. https://myorg.omniapp.co")
        if not UUID_RE.match(str(m.get("model_id", ""))):
            die(f"{path}: models[{i}].model_id must be a UUID")
        mp = str(m.get("model_path", "")).strip("/")
        if not mp or mp.startswith("..") or "/../" in mp or PurePosixPath(mp).is_absolute():
            die(f"{path}: models[{i}].model_path must be a relative path inside the repo")
        if mp in seen_paths:
            die(f"{path}: duplicate model_path {mp!r}")
        seen_paths.add(mp)
        m["model_path"] = mp
    return models


def changed_under(base_ref: str, model_path: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD", "--", f"{model_path}/"],
        capture_output=True, text=True, check=False,
    ).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref")
    ap.add_argument("--base-ref")
    ap.add_argument("--path", default=".omni/flow.json")
    args = ap.parse_args()

    models = validate(load(args.ref, args.path), args.path)

    outputs: dict[str, str] = {}
    selected: dict | None = None

    if args.base_ref:
        hits = [(m, changed_under(args.base_ref, m["model_path"])) for m in models]
        touched = [(m, files) for m, files in hits if files]
        if len(touched) > 1:
            die("Changed model YAML spans more than one registered model: "
                + ", ".join(m["model_path"] for m, _ in touched)
                + ". Split the pull request per model.")
        if touched:
            selected, files = touched[0]
            print("Model YAML changed:")
            for f in files:
                print(f"  {f}")
            outputs["model"] = "true"
        else:
            print("No model YAML changed under any registered model_path.")
            outputs["model"] = "false"

    if selected is None and len(models) == 1:
        selected = models[0]
    elif selected is None and not args.base_ref:
        die(f"{args.path} registers {len(models)} models; pass --base-ref so one can be selected.")

    if selected is not None:
        mp = selected["model_path"]
        outputs.update({
            "base_url": selected["base_url"].rstrip("/"),
            "model_id": selected["model_id"],
            "model_path": mp,
            "model_dir": str(PurePosixPath(mp).parent) if "/" in mp else ".",
            "base_branch": str(selected.get("base_branch", "") or ""),
        })
        print(f"Selected model {outputs['model_id']} at {mp} on {outputs['base_url']}")

    gh_out = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{k}={v}\n" for k, v in outputs.items())
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(lines)
    else:
        sys.stdout.write(lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
