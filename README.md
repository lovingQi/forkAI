# forkAI — 智能叉车 AI 语音控制系统

车载工控机部署的语音控制与监控前端。对接 jarvis HTTP/WebSocket，提供监控总览 + 离线语音点动/任务/车况问答。

## 结构

- `apps/web` — Vue3 监控总览 + 语音条 + 任务流编辑器
- `services/core` — **V2 唯一后端** forkai-core（FastAPI，:19000，静态托管 apps/web/dist）
- `services/mock-jarvis` — 车端模拟（:8080）
- `services/llm-sidecar` — llama-server NLU 兜底侧车（:19002）
- `services/voice-gateway` / `services/speech` — **V1 已冻结禁用**，请勿启动或修改
- `packages/shared` — 共享类型与协议
- `deploy` — systemd / 二维码脚本
- `docs` — 需求冻结与验收

## 快速开始（V2）

```bash
./start-v2.sh        # 一键起 mock-jarvis(:8080) + llama-server(:19002) + forkai-core(:19000)
npm run dev:web      # 可选：前端热更新（vite :5173，代理到 :19000）
```

浏览器打开 `http://<车IP>:19000`：配对 → 现场解锁 → 文本/PTT 下发指令。

> **注意**：V1（voice-gateway/forkweb/speech）已冻结禁用，旧命令仅以 `dev:legacy:*` 前缀保留作历史参考；V1 需 node≥20 才能运行（系统 node16 会崩），请勿再启动。V2 验收见 `docs/acceptance-v2.md`。

验收清单见 `docs/acceptance.md`（V1 历史）。需求冻结见 `docs/requirements-v1.md`。
