#!/usr/bin/env bash
# Detect whether the host uses musl libc (e.g. Alpine) and write
# `musl=true|false` to $GITHUB_OUTPUT for the calling step to branch on.
#
# actions/setup-python only publishes glibc CPython builds. On musl they can't
# run, and setup-python also exports LD_LIBRARY_PATH/pythonLocation into
# $GITHUB_ENV — which then corrupts any musl python (such as a uv venv) that
# later steps in the caller's job rely on. Callers use this flag to skip
# setup-python on musl and fall back to the ambient python3.
set -eu

if [ -f /etc/alpine-release ] || { command -v ldd >/dev/null 2>&1 && ldd --version 2>&1 | grep -qi musl; }; then
  musl=true
else
  musl=false
fi

echo "Detected libc: musl=$musl"
echo "musl=$musl" >> "$GITHUB_OUTPUT"
