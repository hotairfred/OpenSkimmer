#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if [ ! -x .venv/bin/python ]; then
    echo "Missing .venv; see ../../docs/SPARKGAP_MACOS_INSTALL.md" >&2
    exit 1
fi

exec .venv/bin/python sparkgap.py --config skimmer_web888_macos.json "$@"
