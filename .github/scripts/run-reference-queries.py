#!/usr/bin/env python3
"""Run reference queries against an Omni model and compare results to expected.

Each fixture is a YAML file in tests/reference-queries/*.yaml:

    name: orders_grand_total
    description: Total order count across all time
    query:
      table: "Order Items"
      fields: ["order_items.count"]
      join_paths_from_topic_name: "Order Items"
    expect:
      row_count: 1
      rows:
        - [4018]                  # column values in field order; numbers OR strings
    tolerance: 0                   # optional; default 0 (exact)

The runner invokes `omni query run --resultType csv` for each fixture, parses
the CSV, normalizes numeric strings (strips thousands commas), and compares
cell-by-cell to `expect.rows`. Output is a JSON list of normalized findings
consumable by diff-findings.py (kind=queries):

    [
      {
        "key": "<stable hash>",
        "severity": "error" | "info",
        "rule": "row_count" | "row_mismatch" | "execution_error",
        "location": "<fixture name>",
        "message": "<human-readable>",
        "raw": {...}
      },
      ...
    ]

Usage:
    run-reference-queries.py <model_id> [--branch-id <id>] [--dir DIR] [--out FILE]

Exit code is always 0; callers consume the JSON output.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:
    print("ERROR: PyYAML is required. `pip install pyyaml`.", file=sys.stderr)
    sys.exit(2)


def _key(*parts: Any) -> str:
    raw = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _normalize_cell(s: str) -> Any:
    """Strip thousands commas; try int then float; fall back to original string."""
    if s is None:
        return None
    t = s.strip()
    if t == "":
        return ""
    # numbers with thousands separators: "1,234", "1,234.56", "-1,234"
    stripped = t.replace(",", "")
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        pass
    return t


def _cells_equal(actual: Any, expected: Any, tolerance: float) -> bool:
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if tolerance and tolerance > 0:
            return abs(float(actual) - float(expected)) <= float(tolerance)
        return float(actual) == float(expected)
    return str(actual) == str(expected)


def _run_query(model_id: str, branch_id: str | None, query: dict, profile: str | None) -> tuple[str | None, str | None]:
    """Return (csv_text, error_message). On error csv_text is None.

    When `branch_id` is set, it is used as the modelId for the query — branch
    model IDs are queryable directly. The API does not accept a separate
    branchId field inside the query body.
    """
    effective_model_id = branch_id or model_id
    body = {"query": {**query, "modelId": effective_model_id}, "resultType": "csv"}
    args = ["omni"]
    if profile:
        args += ["-p", profile]
    args += ["query", "run", "--body", json.dumps(body), "-o", "json"]
    res = subprocess.run(args, capture_output=True, text=True)
    if res.returncode != 0:
        return None, (res.stderr.strip() or res.stdout.strip())[:1000]
    out = res.stdout
    # When resultType=csv, omni returns raw CSV (not JSON-wrapped). When the
    # query fails inside the planner, omni returns JSON with error_message.
    if out.lstrip().startswith("{"):
        # error path: parse the JSON stream and surface error_message
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("status") == "FAILED" or d.get("error_message"):
                return None, d.get("error_message") or "Query failed"
        return None, "Unexpected JSON response on CSV result type"
    return out, None


def _compare(actual_csv: str, expect: dict, tolerance: float) -> list[dict]:
    """Return a list of mismatch findings (empty if all good)."""
    reader = csv.reader(io.StringIO(actual_csv))
    rows = list(reader)
    if not rows:
        return [{"rule": "row_count", "message": "Query returned no header"}]
    header, *data_rows = rows
    findings: list[dict] = []
    expected_count = expect.get("row_count")
    if expected_count is not None and expected_count != len(data_rows):
        findings.append({
            "rule": "row_count",
            "message": f"Expected {expected_count} row(s), got {len(data_rows)}",
        })
    expected_rows = expect.get("rows")
    if expected_rows is not None:
        for i, exp_row in enumerate(expected_rows):
            if i >= len(data_rows):
                findings.append({
                    "rule": "missing_row",
                    "message": f"Row {i+1} missing in actual results",
                    "expected": exp_row,
                })
                continue
            actual_cells = [_normalize_cell(c) for c in data_rows[i]]
            if len(exp_row) != len(actual_cells):
                findings.append({
                    "rule": "column_count",
                    "message": f"Row {i+1}: expected {len(exp_row)} columns, got {len(actual_cells)}",
                    "expected": exp_row,
                    "actual": actual_cells,
                })
                continue
            for j, (exp_v, act_v) in enumerate(zip(exp_row, actual_cells)):
                if not _cells_equal(act_v, exp_v, tolerance):
                    col_name = header[j] if j < len(header) else f"col{j}"
                    findings.append({
                        "rule": "row_mismatch",
                        "message": f"Row {i+1} col '{col_name}': expected {exp_v!r}, got {act_v!r}",
                        "row_index": i,
                        "column": col_name,
                        "expected": exp_v,
                        "actual": act_v,
                    })
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model_id")
    ap.add_argument("--branch-id", default=None)
    ap.add_argument("--dir", default="tests/reference-queries")
    ap.add_argument("--profile", default=os.environ.get("OMNI_PROFILE_NAME"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    specs_dir = Path(args.dir)
    fixtures = sorted(specs_dir.glob("*.yaml")) + sorted(specs_dir.glob("*.yml"))
    if not fixtures:
        print(f"::warning::No reference query fixtures found in {specs_dir}", file=sys.stderr)

    out_findings: list[dict] = []
    summary = {"total": 0, "passed": 0, "failed": 0}

    for path in fixtures:
        with open(path) as f:
            spec = yaml.safe_load(f)
        if not isinstance(spec, dict):
            continue
        name = spec.get("name") or path.stem
        query = spec.get("query") or {}
        expect = spec.get("expect") or {}
        tolerance = float(spec.get("tolerance") or 0)
        summary["total"] += 1

        csv_text, err = _run_query(args.model_id, args.branch_id, query, args.profile)
        if err is not None:
            out_findings.append({
                "key": _key(name, "execution_error", err),
                "severity": "error",
                "rule": "execution_error",
                "location": name,
                "message": err,
                "raw": {"fixture": str(path), "spec": spec},
            })
            summary["failed"] += 1
            continue

        mismatches = _compare(csv_text, expect, tolerance)
        if mismatches:
            for m in mismatches:
                out_findings.append({
                    "key": _key(name, m["rule"], m.get("row_index"), m.get("column"), m["message"]),
                    "severity": "error",
                    "rule": m["rule"],
                    "location": name,
                    "message": m["message"],
                    "raw": {"fixture": str(path), **m},
                })
            summary["failed"] += 1
        else:
            summary["passed"] += 1

    payload = {"issues": out_findings, "summary": summary}
    text = json.dumps(payload, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text)
    print(f"\nReference queries: {summary['passed']}/{summary['total']} passed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
