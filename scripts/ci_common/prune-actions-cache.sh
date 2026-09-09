#!/usr/bin/env bash
# Copyright (c) 2006-Present, Redis Ltd.
# All rights reserved.
#
# Licensed under your choice of the Redis Source Available License 2.0
# (RSALv2); or (b) the Server Side Public License v1 (SSPLv1); or (c) the
# GNU Affero General Public License v3 (AGPLv3).

# Prune the oldest GitHub Actions cache entries for a key prefix, keeping the
# newest KEEP_COUNT and deleting at most MAX_DELETE_PER_RUN per invocation.
# Driven entirely by env vars so cache-maintenance.yml's `env:` block is the
# single place inputs are wired — see that workflow for parameter docs.
#
# Required: REPO, KEY_PREFIX, KEEP_COUNT, MAX_DELETE_PER_RUN
# Optional: LABEL (default: cache)
# If GITHUB_OUTPUT is set, numeric results are also written as step outputs.

set -euo pipefail

require_non_empty() {
  local name="$1"
  local value="$2"
  if [ -z "$value" ]; then
    echo "$name is required" >&2
    exit 1
  fi
}

require_non_negative_integer() {
  local name="$1"
  local value="$2"
  if ! [[ "$value" =~ ^[0-9]+$ ]]; then
    echo "$name must be a non-negative integer (got: $value)" >&2
    exit 1
  fi
}

REPO="${REPO:-}"
KEY_PREFIX="${KEY_PREFIX:-}"
KEEP_COUNT="${KEEP_COUNT:-}"
MAX_DELETE_PER_RUN="${MAX_DELETE_PER_RUN:-}"
LABEL="${LABEL:-cache}"

require_non_empty "REPO" "$REPO"
require_non_empty "KEY_PREFIX" "$KEY_PREFIX"
require_non_empty "KEEP_COUNT" "$KEEP_COUNT"
require_non_empty "MAX_DELETE_PER_RUN" "$MAX_DELETE_PER_RUN"
require_non_negative_integer "KEEP_COUNT" "$KEEP_COUNT"
require_non_negative_integer "MAX_DELETE_PER_RUN" "$MAX_DELETE_PER_RUN"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

gh cache list \
  --repo "$REPO" \
  --key "$KEY_PREFIX" \
  --limit 20000 \
  --sort last_accessed_at \
  --order asc \
  --json id,sizeInBytes > "$tmp"

total_count="$(jq 'length' "$tmp")"
total_size_bytes="$(jq '[.[].sizeInBytes] | add // 0' "$tmp")"
delete_count=0
delete_size_bytes=0
deleted_count=0
deleted_size_bytes=0
failed_count=0
failed_size_bytes=0

if [ "$total_count" -le "$KEEP_COUNT" ]; then
  echo "$LABEL cache count ($total_count) is within keep threshold ($KEEP_COUNT)"
else
  delete_count=$((total_count - KEEP_COUNT))
  if [ "$delete_count" -gt "$MAX_DELETE_PER_RUN" ]; then
    delete_count="$MAX_DELETE_PER_RUN"
  fi

  delete_size_bytes="$(jq --argjson n "$delete_count" '[.[0:$n][].sizeInBytes] | add // 0' "$tmp")"
  echo "Deleting $delete_count oldest $LABEL caches (total=$total_count, keep=$KEEP_COUNT, max_per_run=$MAX_DELETE_PER_RUN)"

  while read -r cache_id cache_size_bytes; do
    if gh cache delete "$cache_id" --repo "$REPO"; then
      deleted_count=$((deleted_count + 1))
      deleted_size_bytes=$((deleted_size_bytes + cache_size_bytes))
    else
      failed_count=$((failed_count + 1))
      failed_size_bytes=$((failed_size_bytes + cache_size_bytes))
      echo "Failed to delete $LABEL cache id=$cache_id (continuing)"
    fi
  done < <(jq -r --argjson n "$delete_count" '.[0:$n][] | "\(.id) \(.sizeInBytes)"' "$tmp")
fi

bytes_to_mib() {
  awk "BEGIN { printf \"%.2f\", ($1 / 1024 / 1024) }"
}

echo "Summary for $LABEL caches:"
echo "  total_found=$total_count ($(bytes_to_mib "$total_size_bytes") MiB)"
echo "  selected_for_deletion=$delete_count ($(bytes_to_mib "$delete_size_bytes") MiB)"
echo "  deleted=$deleted_count ($(bytes_to_mib "$deleted_size_bytes") MiB)"
echo "  failed=$failed_count ($(bytes_to_mib "$failed_size_bytes") MiB)"

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "total_count=$total_count"
    echo "total_size_bytes=$total_size_bytes"
    echo "delete_count=$delete_count"
    echo "delete_size_bytes=$delete_size_bytes"
    echo "deleted_count=$deleted_count"
    echo "deleted_size_bytes=$deleted_size_bytes"
    echo "failed_count=$failed_count"
    echo "failed_size_bytes=$failed_size_bytes"
  } >> "$GITHUB_OUTPUT"
fi
