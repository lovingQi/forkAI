#!/bin/bash
# 测试用引擎入口。忽略 Cartographer 的命令行参数，按 FORKAI_FAKE_ENGINE_ENDPOINT 监听。
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
py="$here/../.venv/bin/python"
if [[ ! -x "$py" ]]; then
  py="$(command -v python3)"
fi
exec "$py" "$here/fake_engine.py" "$@"
