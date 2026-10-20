#!/usr/bin/env bash
# 开发机建图演示：假 Foxglove Bridge + 假引擎。不能作为真车或真引擎验收。
# 用法: ./start-mapping-demo.sh        启动演示，并调用 ./start-v2.sh
#       ./start-mapping-demo.sh stop   只停演示拉起的 Bridge 和建图服务
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG=/tmp/forkai-v2
mkdir -p "$LOG"
PID_BRIDGE="$LOG/demo-bridge.pid"
PID_MAP="$LOG/demo-mapping.pid"
CFG="$ROOT/services/mapping/config/mapping.demo.yaml"
PY="$ROOT/services/mapping/.venv/bin/python"

alive() {
  [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null
}

listening() {
  ss -ltn | awk '{print $4}' | grep -qE "[:.]${1}$"
}

stop_pid() {
  local file="$1"
  if alive "$file"; then
    local pid
    pid="$(cat "$file")"
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.3
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$file"
}

if [ "${1:-}" = "stop" ]; then
  stop_pid "$PID_BRIDGE"
  stop_pid "$PID_MAP"
  exit 0
fi

if [ ! -x "$PY" ]; then
  echo "[mapping-demo] 没有 services/mapping/.venv，先安装建图服务依赖"
  exit 1
fi

if listening 8765 && ! alive "$PID_BRIDGE"; then
  echo "[mapping-demo] 8765 已被其他进程占用。确认后可执行: $0 stop 只停本脚本拉起的进程"
  exit 1
fi
if listening 19010 && ! alive "$PID_MAP"; then
  echo "[mapping-demo] 19010 已被其他建图服务占用。本脚本不会结束它。"
  exit 1
fi

if ! alive "$PID_BRIDGE"; then
  (cd "$ROOT/services/mapping" && nohup "$PY" devtools/fake_bridge.py --port 8765 > "$LOG/demo-bridge.log" 2>&1 & echo $! > "$PID_BRIDGE")
fi
if ! alive "$PID_MAP"; then
  (cd "$ROOT/services/mapping" && FORKAI_MAPPING_CONFIG="$CFG" nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 19010 > "$LOG/demo-mapping.log" 2>&1 & echo $! > "$PID_MAP")
fi

for _ in $(seq 1 30); do
  listening 8765 && listening 19010 && break
  sleep 0.3
done

echo "[mapping-demo] bridge=$(alive "$PID_BRIDGE" && echo up || echo down) mapping=$(curl -s -m 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:19010/api/v1/mapping/status)"
"$ROOT/start-v2.sh"
