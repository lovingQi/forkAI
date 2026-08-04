# forkAI — 智能叉车 AI 语音控制系统

车载工控机部署的语音控制与监控前端。对接 jarvis HTTP/WebSocket，提供监控总览 + 离线语音点动/任务/车况问答。

## 结构

- `apps/web` — Vue3 监控总览 + 语音条
- `services/voice-gateway` — 配对、现场会话、意图、看门狗、代理 jarvis
- `services/speech` — 离线 ASR/TTS 适配（V1 默认 mock，可替换真引擎）
- `packages/shared` — 共享类型与协议
- `deploy` — systemd / 二维码脚本
- `docs` — 需求冻结与验收

## 快速开始

```bash
npm install
# 终端1
npm run dev:speech
# 终端2（默认代理 jarvis http://127.0.0.1:8080）
npm run dev:gateway
# 终端3
npm run dev:web
```

浏览器打开 `http://<车IP>:5173`：配对 → 现场解锁 → 文本/PTT 下发指令。

验收清单见 `docs/acceptance.md`。需求冻结见 `docs/requirements-v1.md`。
