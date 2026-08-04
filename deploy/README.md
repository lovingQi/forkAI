# 厂网部署说明

1. 工控机安装 Node 20+，将本仓库放到 `/opt/forkai`。
2. `npm install && npm run build -w @forkai/shared`（可选）后：
   - `npm run build -w @forkai/web`
   - gateway 用 `tsx` 或 `npm run build -w @forkai/voice-gateway && npm run start -w @forkai/voice-gateway`
3. 安装 systemd：`deploy/forkai-speech.service`、`deploy/forkai-gateway.service`
4. jarvis-g 监听 `127.0.0.1:8080`；gateway 代理其 HTTP/WS。
5. 厂 WiFi 下手机访问 `http://<车IP>:19000/`（静态由 gateway 托管 `apps/web/dist`）。
6. 车身码：`node deploy/scripts/gen-vehicle-qr.mjs --host <车IP> --port 19000 --id fork-01`

## 安全

- 厂网内须配对码才能控制。
- 点动须现场码；语音「停」≠ 实体急停。
- 勿将 gateway 暴露到公网。
