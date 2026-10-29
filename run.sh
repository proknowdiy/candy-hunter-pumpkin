#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

exec python3 "$PROJECT_DIR/app/candy_hunter_pumpkin.py" "$@"
