#!/usr/bin/env bash
# Resolve the Omni branch ID for the current PR's git head ref.
#
# Convention: Omni branch name == git head ref (1:1 mapping).
#
# Required env:
#   OMNI_MODEL_ID  -- the SHARED base model ID
#   GIT_REF        -- branch name to resolve (defaults to GITHUB_HEAD_REF, then GITHUB_REF_NAME)
#
# Writes to $GITHUB_OUTPUT (when set):
#   branch_name=<name>
#   branch_id=<uuid>
#
# Exits non-zero if no matching branch exists.

set -euo pipefail

REF="${GIT_REF:-${GITHUB_HEAD_REF:-${GITHUB_REF_NAME:-}}}"
if [ -z "$REF" ]; then
  echo "::error::No git ref available (set GIT_REF, GITHUB_HEAD_REF, or GITHUB_REF_NAME)" >&2
  exit 2
fi

if [ -z "${OMNI_MODEL_ID:-}" ]; then
  echo "::error::OMNI_MODEL_ID is not set" >&2
  exit 2
fi

BRANCH_JSON=$(omni models list \
  --modelkind BRANCH \
  --basemodelid "$OMNI_MODEL_ID" \
  --name "$REF" \
  --pagesize 100 \
  --compact -o json)

BRANCH_ID=$(python3 -c '
import json, sys
data = json.loads(sys.argv[1])
ref = sys.argv[2]
matches = [r for r in data.get("records", []) if r.get("name") == ref and not r.get("deletedAt")]
if not matches:
    sys.exit(1)
matches.sort(key=lambda r: r.get("updatedAt", ""), reverse=True)
print(matches[0]["id"])
' "$BRANCH_JSON" "$REF" || true)

if [ -z "$BRANCH_ID" ]; then
  echo "::error::No Omni branch named '$REF' found on model $OMNI_MODEL_ID. Create the branch in Omni before opening the PR." >&2
  exit 1
fi

echo "Resolved Omni branch '$REF' -> $BRANCH_ID"

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "branch_name=$REF"
    echo "branch_id=$BRANCH_ID"
  } >> "$GITHUB_OUTPUT"
fi
