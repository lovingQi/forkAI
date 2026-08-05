#!/usr/bin/env bash
# 启动 llama.cpp llama-server 侧车（Qwen2-0.5B-Instruct Q4_K_M）。
# 用法: services/llm-sidecar/run-llama-server.sh
# 环境变量可覆盖: LLM_PORT / LLM_THREADS / LLM_CTX / LLM_MODEL
set -u

SIDECAR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "$SIDECAR_DIR/../core" && pwd)"

BIN="$SIDECAR_DIR/bin/llama-server"
MODEL="${LLM_MODEL:-$CORE_DIR/models/llm/qwen2-0_5b-instruct-q4_k_m.gguf}"
PORT="${LLM_PORT:-19002}"
THREADS="${LLM_THREADS:-4}"
CTX="${LLM_CTX:-2048}"

if [ ! -x "$BIN" ]; then
  echo "[llm-sidecar] llama-server 不存在或不可执行: $BIN" >&2
  echo "[llm-sidecar] 请先编译: cd services/llm-sidecar/src && cmake -B build -G Ninja -DGGML_NATIVE=OFF -DLLAMA_CURL=OFF && cmake --build build --target llama-server && cp build/bin/llama-server ../bin/" >&2
  exit 1
fi
if [ ! -f "$MODEL" ]; then
  echo "[llm-sidecar] 模型缺失: $MODEL" >&2
  exit 1
fi

echo "[llm-sidecar] llama-server http://127.0.0.1:$PORT model=$(basename "$MODEL") threads=$THREADS ctx=$CTX"
"$BIN" \
  --host 127.0.0.1 --port "$PORT" \
  -m "$MODEL" \
  -c "$CTX" -t "$THREADS" \
  --log-disable &
SERVER_PID=$!

# warmup：等待端口就绪后发一次最小请求，使模型常驻内存
for _ in $(seq 1 60); do
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/health"; then
    break
  fi
  sleep 1
done
curl -s -o /dev/null --max-time 120 -X POST "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"warmup","messages":[{"role":"user","content":"你好"}],"max_tokens":1,"temperature":0}' \
  && echo "[llm-sidecar] warmup 完成，模型已常驻" || echo "[llm-sidecar] warmup 失败（服务仍可用首请求会慢）" >&2

wait $SERVER_PID
