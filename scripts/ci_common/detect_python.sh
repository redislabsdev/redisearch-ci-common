#!/usr/bin/env bash
# Write `usable=true|false` to $GITHUB_OUTPUT: whether the ambient python3 can
# run the flaky DB CLI — an activated virtualenv, >= 3.10, pip importable.
#
# Callers use this to skip actions/setup-python, which hard-fails inside
# non-Ubuntu containers. Requiring a venv (rather than any python3) avoids
# PEP 668 externally-managed system pythons, where `pip install` refuses.
# Run under a login shell so a venv activated in the profile is visible.
set -eu

if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.prefix != sys.base_prefix else 1)' 2>/dev/null \
    && python3 -m pip --version >/dev/null 2>&1; then
  usable=true
else
  usable=false
fi

echo "Ambient python3 usable: $usable ($(python3 --version 2>/dev/null || echo 'not found'))"
echo "usable=$usable" >> "$GITHUB_OUTPUT"
