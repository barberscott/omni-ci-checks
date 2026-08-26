#!/usr/bin/env python3
"""Extract findings JSON from claude-code-action outputs.

The action exposes two relevant outputs:
  - structured_output: only populated when --json-schema is honored (which the
    action does not currently support via claude_args).
  - execution_file: path to the full Claude Code execution stream — a JSON
    array of SDKMessage objects from @anthropic-ai/claude-agent-sdk. The
    terminal message has type="result", subtype="success", result="<text>".

This script tries them in order and emits the parsed JSON to --out. On any
failure, it writes a safe placeholder so the workflow can continue.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PLACEHOLDER = {
    "summary": "Claude review did not return parseable JSON (see workflow logs).",
    "findings": [],
}


def _try_parse(text: str) -> dict | None:
    if not text:
        return None
    s = text.strip()
    # Try direct parse.
    try:
        d = json.loads(s)
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        pass
    # Try stripping markdown code fences.
    if s.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        stripped = re.sub(r"\s*```$", "", stripped)
        try:
            d = json.loads(stripped)
            return d if isinstance(d, dict) else None
        except json.JSONDecodeError:
            pass
    # Find the largest {...} block.
    matches = re.findall(r"\{(?:[^{}]|\{[^{}]*\})*\}", s, re.DOTALL)
    for m in sorted(matches, key=len, reverse=True):
        try:
            d = json.loads(m)
            if isinstance(d, dict) and "findings" in d:
                return d
        except json.JSONDecodeError:
            continue
    return None


def _final_message(execution: Any) -> str | None:
    """Walk the action's execution payload to find the last assistant text."""
    if isinstance(execution, dict):
        # Some shapes nest messages under a top-level key.
        for key in ("messages", "history", "events"):
            if key in execution and isinstance(execution[key], list):
                return _final_message(execution[key])
        # Or the file is directly the final result.
        for key in ("final_message", "result", "response", "content", "text"):
            if key in execution and isinstance(execution[key], str):
                return execution[key]
    if isinstance(execution, list):
        # claude-code-action SDK format: messages are SDKMessage objects from
        # @anthropic-ai/claude-agent-sdk.  The terminal message has:
        #   { type: "result", subtype: "success", result: "<claude's text>" }
        # Search for this first since it's authoritative.
        for item in reversed(execution):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "result" and item.get("subtype") == "success":
                result_text = item.get("result")
                if isinstance(result_text, str) and result_text.strip():
                    print(
                        f"Found SDK result message (len={len(result_text)})",
                        file=sys.stderr,
                    )
                    return result_text

        # Fallback: search backwards for the last assistant turn.
        for item in reversed(execution):
            if not isinstance(item, dict):
                continue
            role = item.get("role") or item.get("type")
            if role and role not in ("assistant", "model"):
                continue
            content = item.get("content") or item.get("text") or item.get("message")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                # Anthropic-style content blocks: [{type:"text", text:"..."}]
                texts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                joined = "\n".join(t for t in texts if t).strip()
                if joined:
                    return joined

        # Last resort: dump the whole file and grep for a {findings:[...]} block.
        raw = json.dumps(execution)
        parsed = _try_parse(raw)
        if parsed is not None:
            print("Extracted via full-file grep fallback", file=sys.stderr)
            return json.dumps(parsed)

    return None


def _result_meta(execution: Any) -> dict:
    """Pull cost/usage/turn telemetry from the terminal SDK result message.

    The result message shape (from @anthropic-ai/claude-agent-sdk) is:
      { "type": "result", "subtype": "success",
        "total_cost_usd": 0.30, "num_turns": 8, "duration_ms": 108756,
        "usage": { "input_tokens", "output_tokens",
                   "cache_creation_input_tokens", "cache_read_input_tokens" } }

    Returns {} when no result message is present.
    """
    if not isinstance(execution, list):
        return {}
    for item in reversed(execution):
        if isinstance(item, dict) and item.get("type") == "result":
            usage = item.get("usage") or {}
            meta = {
                "total_cost_usd": item.get("total_cost_usd"),
                "num_turns": item.get("num_turns"),
                "duration_ms": item.get("duration_ms"),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            }
            print(
                f"Result meta: cost=${meta['total_cost_usd']} "
                f"turns={meta['num_turns']} duration_ms={meta['duration_ms']}",
                file=sys.stderr,
            )
            return meta
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execution-file", default="")
    ap.add_argument("--structured-output", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--usage-out",
        default="",
        help="Optional path to write cost/usage telemetry JSON ({} if unavailable).",
    )
    args = ap.parse_args()

    # Read the execution file once up front (used for both findings and usage).
    execution = None
    if args.execution_file:
        try:
            raw_text = Path(args.execution_file).read_text()
            print(f"execution_file size: {len(raw_text)} bytes", file=sys.stderr)
            execution = json.loads(raw_text)
            print(
                f"execution type: {type(execution).__name__}"
                + (f", len={len(execution)}" if isinstance(execution, list) else ""),
                file=sys.stderr,
            )
        except Exception as e:
            print(f"::warning::Could not read execution_file: {e}", file=sys.stderr)
            execution = None

    # Always emit usage telemetry (empty object when unavailable) so the
    # downstream render step has a stable file to read.
    if args.usage_out:
        Path(args.usage_out).write_text(json.dumps(_result_meta(execution), indent=2))

    # 1. structured_output wins if it parses.
    parsed = _try_parse(args.structured_output)
    if parsed is not None:
        Path(args.out).write_text(json.dumps(parsed, indent=2))
        print(f"Used structured_output (keys: {sorted(parsed.keys())})", file=sys.stderr)
        return 0

    # 2. Otherwise, parse the execution file's final assistant message.
    if execution is not None:
        final = _final_message(execution)
        if final:
            parsed = _try_parse(final)
            if parsed is not None:
                Path(args.out).write_text(json.dumps(parsed, indent=2))
                print(
                    f"Used execution_file final message (keys: {sorted(parsed.keys())})",
                    file=sys.stderr,
                )
                return 0
            else:
                print(
                    f"::warning::Final assistant message did not contain parseable JSON:\n{final[:500]}",
                    file=sys.stderr,
                )
        else:
            # Emit message structure summary for debugging
            if isinstance(execution, list):
                types = [
                    f"{m.get('type','?')}:{m.get('subtype','')}"
                    for m in execution
                    if isinstance(m, dict)
                ]
                print(
                    f"::warning::_final_message returned None. Message types: {types}",
                    file=sys.stderr,
                )

    # 3. Give up gracefully — emit placeholder so the workflow still posts a comment.
    print("::warning::No parseable JSON from Claude; emitting placeholder.", file=sys.stderr)
    Path(args.out).write_text(json.dumps(PLACEHOLDER, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
