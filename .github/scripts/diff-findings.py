#!/usr/bin/env python3
"""Compute net-new findings: branch issues minus base issues.

Reads two JSON files describing the same kind of issues (model validator,
content validator, query evals, etc.) and writes a JSON document containing
only the issues present on the branch but not on the base.

Usage:
    diff-findings.py <base.json> <branch.json> <kind> [--out file]

The script is intentionally agnostic about source schemas. It delegates to an
"adapter" per `kind` that normalizes input into a flat list of dicts with:

    { "key": str, "severity": "error"|"warning"|"info", "message": str, ... }

Output schema (stdout or --out):

    {
      "kind": "<kind>",
      "base_total": <int>,
      "branch_total": <int>,
      "net_new": [<issue>, ...],
      "fixed":   [<issue>, ...],
      "errors":   [<issue>, ...],    # subset of net_new with severity=="error"
      "warnings": [<issue>, ...],
      "infos":    [<issue>, ...]
    }

Exit code is always 0 — the caller decides whether to fail the job based on
counts in the output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any, Callable


def _stable_key(*parts: Any) -> str:
    raw = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ---------- adapters ----------------------------------------------------------

def adapt_model_validate(doc: Any) -> list[dict]:
    """Normalize `omni models validate` output.

    Real shape (as of CLI v1.0.4): a JSON array of issues, each:
        { "message": str, "yaml_path": str, "auto_fix": {...}, "is_warning": bool }
    """
    issues = doc if isinstance(doc, list) else (doc.get("issues") or doc.get("records") or [])
    out = []
    for i in issues:
        is_warning = bool(i.get("is_warning"))
        yaml_path = i.get("yaml_path") or ""
        msg = i.get("message") or ""
        key = _stable_key(yaml_path, msg)
        out.append({
            "key": key,
            "severity": "warning" if is_warning else "error",
            "rule": None,
            "location": yaml_path,
            "message": msg,
            "raw": i,
        })
    return out


def adapt_content_validate(doc: Any) -> list[dict]:
    """Normalize `omni models content-validator-get` output.

    Real shape (CLI v1.0.4):
        {
          "branch": ...,
          "model_id": "...",
          "content": [
            {
              "document_id": "...",
              "identifier": "abc123",
              "name": "...",
              "folder": "/path" or null,
              "queries_and_issues": [
                {
                  "query_name": "...",
                  "query_presentation_id": "...",
                  "query_id_map_key": "1",
                  "issues": ["error message", ...]
                }
              ],
              "dashboard_filter_issues": [...]
            }
          ]
        }

    Issue messages are bare strings, not structured objects.
    """
    if not isinstance(doc, dict):
        return []
    out = []
    for c in doc.get("content", []) or []:
        doc_id = c.get("document_id")
        ident = c.get("identifier")
        name = c.get("name") or ident or doc_id
        folder = c.get("folder") or ""
        for q in c.get("queries_and_issues", []) or []:
            qname = q.get("query_name") or q.get("query_id_map_key") or ""
            qpid = q.get("query_presentation_id")
            for msg in q.get("issues", []) or []:
                out.append({
                    "key": _stable_key(doc_id, "query", qpid, msg),
                    "severity": "error",
                    "rule": None,
                    "location": f"{name} › {qname}" + (f" [{folder}]" if folder else ""),
                    "message": msg if isinstance(msg, str) else str(msg),
                    "raw": {
                        "document_id": doc_id, "identifier": ident,
                        "query_presentation_id": qpid, "query_name": qname,
                    },
                })
        for f in c.get("dashboard_filter_issues", []) or []:
            fid = f.get("filter_id") or f.get("field") or f.get("id") if isinstance(f, dict) else None
            msg = f.get("message") if isinstance(f, dict) else str(f)
            out.append({
                "key": _stable_key(doc_id, "filter", fid, msg),
                "severity": "error",
                "rule": None,
                "location": f"{name} › filter:{fid or '?'}",
                "message": msg or "",
                "raw": {"document_id": doc_id, "filter": f},
            })
    return out


def adapt_generic(doc: Any) -> list[dict]:
    """Pass-through adapter for already-normalized inputs."""
    if isinstance(doc, dict) and "issues" in doc:
        return list(doc["issues"])
    if isinstance(doc, list):
        return list(doc)
    return []


ADAPTERS: dict[str, Callable[[Any], list[dict]]] = {
    "model": adapt_model_validate,
    "content": adapt_content_validate,
    "queries": adapt_generic,
    "ai-evals": adapt_generic,
    "generic": adapt_generic,
}


# ---------- main --------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", help="Path to base findings JSON")
    ap.add_argument("branch", help="Path to branch findings JSON")
    ap.add_argument("kind", choices=sorted(ADAPTERS), help="Adapter to apply")
    ap.add_argument("--out", help="Write result here instead of stdout")
    args = ap.parse_args()

    with open(args.base) as f:
        base_doc = json.load(f)
    with open(args.branch) as f:
        branch_doc = json.load(f)

    adapt = ADAPTERS[args.kind]
    base = adapt(base_doc)
    branch = adapt(branch_doc)

    base_keys = {i["key"] for i in base}
    branch_keys = {i["key"] for i in branch}

    net_new = [i for i in branch if i["key"] not in base_keys]
    fixed = [i for i in base if i["key"] not in branch_keys]

    by_sev = lambda level: [i for i in net_new if i.get("severity") == level]

    result = {
        "kind": args.kind,
        "base_total": len(base),
        "branch_total": len(branch),
        "net_new": net_new,
        "fixed": fixed,
        "errors": by_sev("error"),
        "warnings": by_sev("warning"),
        "infos": by_sev("info"),
    }

    payload = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(payload)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
