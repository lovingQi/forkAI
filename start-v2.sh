#!/usr/bin/env bash
# forkAI V2 一键启动：mock-jarvis(:8080) + forkai-core(:19000)
# V1(voice-gateway/forkweb/speech)已冻结禁用，请勿启动；前端热更新另跑 npm run dev:web
# 云端 ASR/TTS/SiliconFlow LLM 需 FORKAI_TTS_API_KEY；官方 DeepSeek 需 FORKAI_DEEPSEEK_API_KEY
# 用法: ./start-v2.sh（幂等：已在跑的组件自动跳过）
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG=/tmp/forkai-v2
mkdir -p "$LOG"

echo "[start-v2] mock-jarvis :8080"
curl -s -m 2 -o /dev/null http://127.0.0.1:8080/api/state || \
  (cd "$ROOT/services/mock-jarvis" && nohup node src/index.js > "$LOG/mock.log" 2>&1 &)

echo "[start-v2] forkai-core :19000"
curl -s -m 2 -o /dev/null http://127.0.0.1:19000/api/health || \
  (cd "$ROOT/services/core" && nohup .venv/bin/python -m uvicorn app.main:app \
    --host 0.0.0.0 --port 19000 > "$LOG/core.log" 2>&1 &)

# 等待各组件就绪
for url in http://127.0.0.1:8080/api/state http://127.0.0.1:19000/api/health; do
  for _ in $(seq 1 30); do
    curl -s -m 2 -o /dev/null "$url" && break
    sleep 1
  done
done

echo
echo "[start-v2] 状态:"
echo "  mock-jarvis  :8080  http=$(curl -s -m 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/state)"
echo "  forkai-core  :19000 $(curl -s -m 2 http://127.0.0.1:19000/api/health)"
echo
echo "入口: http://127.0.0.1:19000/  (core 静态托管 apps/web/dist；前端热更新: npm run dev:web)"
echo "日志: $LOG/{mock,core}.log"
echo "停止: pkill -f 'mock-jarvis/src/index.js'; pkill -f 'uvicorn app.main:app'"
