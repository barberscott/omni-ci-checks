#!/usr/bin/env python3
"""Extract the prior BP comment's embedded data block for the resolved ledger.

Reads the PR's issue comments (JSON array), finds the best-practices sticky
comment's `<!-- omni-bp-data:v1 <b64> -->` block, and writes its decoded
contents (`{findings, resolved}`) to --out. Writes `{}` when none is found
(first review on the PR, or pre-data-block comment).
"""

from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path

DATA_RE = re.compile(r"<!--\s*omni-bp-data:v1\s+([A-Za-z0-9+/=]+)\s*-->")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comments", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--marker", default="omni-best-practices",
                    help="Sticky-comment marker whose data block to read. Only "
                         "that reviewer's comment is considered, so the Claude "
                         "and Omni-agent ledgers can't cross-contaminate.")
    args = ap.parse_args()

    try:
        comments = json.loads(Path(args.comments).read_text())
    except Exception:
        comments = []

    tag = f"<!-- {args.marker} -->"
    candidates = [c for c in comments if tag in (c.get("body") or "")]

    out: dict = {}
    for c in candidates:
        m = DATA_RE.search(c.get("body") or "")
        if m:
            try:
                out = json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
                break
            except Exception:
                continue

    Path(args.out).write_text(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
