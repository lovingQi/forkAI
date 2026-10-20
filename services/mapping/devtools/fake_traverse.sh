#!/bin/bash
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
py="$here/../.venv/bin/python"
exec "$py" "$here/fake_traverse_engine.py" "$@"
