#!/usr/bin/env bash
set -e
cd -- "$(dirname -- "$0")"
if [ ! -d .venv ]; then
  if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo 'Python 3.11 or newer is required.' >&2
    exit 1
  fi
  python3 -m venv .venv
fi
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo 'The existing .venv is unusable. Rename it and start again.' >&2
  exit 1
fi
exec .venv/bin/python launch.py "$@"
