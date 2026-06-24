"""Shared helpers for Codex-agent resolve/context scripts.

Lets RediSearch CI Codex flows share one set of `gh` CLI / `$GITHUB_OUTPUT` /
context-file helpers instead of each carrying a copy. Intentionally generic: it
knows nothing about any specific workflow, branch, or product feature.

Assumptions when run inside GitHub Actions:
- `gh` is installed and authenticated via env (GH_TOKEN, GH_REPO).
- `git` is on PATH.
- `RUNNER_TEMP` and `GITHUB_OUTPUT` are set.
- Python 3.8+ (callers use `from __future__ import annotations`).

Keep this module deliberately tiny — these helpers exist so resolve scripts
read as logic rather than subprocess plumbing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Iterable, NoReturn


# ---- workflow log / outputs --------------------------------------------------


def log(msg: str) -> None:
    """Print one line to the workflow log."""
    print(msg, flush=True)


def set_output(name: str, value: str) -> None:
    """Append `name=value` to $GITHUB_OUTPUT (single-line scalars only)."""
    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        # Useful for local testing — fall back to a clearly-marked log line.
        log(f"[GITHUB_OUTPUT not set] {name}={value}")
        return
    with open(out_path, "a") as f:
        f.write(f"{name}={value}\n")


def skip(reason: str) -> NoReturn:
    """Log the reason, emit skip=true, and exit 0."""
    log(reason)
    set_output("skip", "true")
    sys.exit(0)


# ---- git ---------------------------------------------------------------------


def git(*args: str, check: bool = True) -> str:
    """Run `git <args>` and return stdout (text)."""
    cmd = ["git", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=check)
    except subprocess.CalledProcessError as e:
        log(f"git command failed: {' '.join(cmd)}\nstderr: {e.stderr.strip()}")
        raise
    return result.stdout


# ---- gh CLI ------------------------------------------------------------------


def gh(*args: str, check: bool = True) -> str:
    """Run `gh <args>` and return stdout (text).

    Raises CalledProcessError on non-zero exit when `check=True`. When
    `check=False`, returns whatever stdout was produced (possibly empty).
    """
    cmd = ["gh", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=check)
    except subprocess.CalledProcessError as e:
        log(f"gh command failed: {' '.join(cmd)}\nstderr: {e.stderr.strip()}")
        raise
    return result.stdout


def gh_json(*args: str) -> Any:
    """`gh <args>` with stdout decoded as a single JSON value (None if empty)."""
    out = gh(*args)
    s = out.strip()
    if not s:
        return None
    return json.loads(s)


# ---- PR helpers --------------------------------------------------------------


def fetch_pr(pr_number: int | str, fields: Iterable[str]) -> dict:
    """`gh pr view <pr> --json <fields>` -> decoded dict."""
    out = gh("pr", "view", str(pr_number), "--json", ",".join(fields))
    return json.loads(out)


# ---- context JSON ------------------------------------------------------------


def write_context(path: str, payload: dict) -> None:
    """Write `payload` as a single-line JSON document to `path` and echo a
    compact summary (not the full content) to the workflow log.

    Bulky byte-of-CI fields (`log_excerpts`) are replaced by a one-line digest
    in the log — they may contain non-masked CI output. The agent reads the
    file directly via its context-file env var, so the log echo is purely for
    human traceability.
    """
    with open(path, "w") as f:
        json.dump(payload, f)

    summary: dict = {}
    for k, v in payload.items():
        if k == "log_excerpts" and isinstance(v, list):
            summary[k] = f"<{len(v)} entries; tails omitted from log>"
        elif k == "context" and isinstance(v, list):
            summary[k] = f"<{len(v)} entries>"
        else:
            summary[k] = v

    log(f"Context written to {path}")
    log(json.dumps(summary))
