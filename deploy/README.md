# forkAI 车载部署说明

把 forkAI（语音网关 + 离线语音 + 监控前端）部署到车载工控机，对接真车 jarvis。

- 支持架构：**x86_64 / aarch64 / armv7l**（脚本自动判断）
- 安装路径：`/usr/local/forkai`
- 组件：Node 20（项目内）+ piper 离线中文 TTS + 前端构建产物 + systemd 自启
- 真车 jarvis 由车端自身提供（HTTP `/api/*` + WS `/ws/high|low`），gateway 只做代理，**不需要 mock-jarvis**

---

## 一、一键部署（推荐）

把整个仓库拷到工控机（不含 `node_modules`、`.node20`、`services/speech/piper`，脚本会重装），然后：

```bash
cd /path/to/forkAI
sudo bash deploy/install.sh
```

指定真车 jarvis 地址（默认 `http://127.0.0.1:8080`）：

```bash
sudo JARVIS_BASE_URL=http://127.0.0.1:10000 bash deploy/install.sh
```

脚本会自动完成：架构判断 → 装 Node20 → `npm install` → 构建前端 → 装 piper+中文模型 → 写 gateway 配置 → 装 systemd 并 `enable --now`。

可用环境变量覆盖默认值：

| 变量 | 默认 | 说明 |
|---|---|---|
| `INSTALL_DIR` | `/usr/local/forkai` | 安装目录 |
| `JARVIS_BASE_URL` | `http://127.0.0.1:8080` | 真车 jarvis web 地址 |
| `FORKAI_PORT` | `19000` | gateway 端口（外部访问入口） |
| `SPEECH_PORT` | `19001` | speech 端口（仅本机） |
| `VEHICLE_ID` | `fork-01` | 车辆编号 |
| `NODE_VER` | `v20.18.1` | Node 版本 |
| `PIPER_MODEL` | `zh_CN-huayan-medium` | piper 中文模型 |

---

## 二、手动分步部署（脚本的人工等价）

```bash
# 1. 架构（x86_64 / aarch64 / armv7l）
uname -m

# 2. 装 Node 20 到项目内（按架构选 x64 / arm64 / armv7l）
cd /usr/local/forkai
curl -fSL -o node.tar.xz https://nodejs.org/dist/v20.18.1/node-v20.18.1-linux-arm64.tar.xz
mkdir -p .node20 && tar -xJf node.tar.xz -C .node20 --strip-components=1 && rm node.tar.xz
export PATH="$PWD/.node20/bin:$PATH"

# 3. 装依赖 + 构建前端
npm install --legacy-peer-deps --no-audit --no-fund
npm run build -w @forkai/web

# 4. 装 piper（按架构选 x86_64 / aarch64 / armv7l）+ 中文模型
cd services/speech/piper
curl -fSL -o piper.tar.gz https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz
tar -xzf piper.tar.gz && rm piper.tar.gz
curl -fSL -o zh_CN-huayan-medium.onnx      https://huggingface.co/csukuangfj/vits-piper-zh_CN-huayan-medium/resolve/main/zh_CN-huayan-medium.onnx
curl -fSL -o zh_CN-huayan-medium.onnx.json https://huggingface.co/csukuangfj/vits-piper-zh_CN-huayan-medium/resolve/main/zh_CN-huayan-medium.onnx.json

# 5. 配 gateway 指向真车 jarvis
#    编辑 services/voice-gateway/config/gateway.config.yaml 的 jarvis.baseUrl

# 6. 装 systemd（deploy/ 下的两个 .service 已按 /usr/local/forkai + 项目内 node 写好）
sudo cp deploy/forkai-speech.service deploy/forkai-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now forkai-speech forkai-gateway
```

---

## 三、配置真车 jarvis 端口

真车 jarvis 的 web 端口以车端实际配置为准。改两处之一：

- 改配置：`services/voice-gateway/config/gateway.config.yaml` 的 `jarvis.baseUrl`
- 或改 systemd：`/etc/systemd/system/forkai-gateway.service` 的 `Environment=JARVIS_BASE_URL=...`

改完重启：

```bash
sudo systemctl restart forkai-gateway
```

> 环境变量 `JARVIS_BASE_URL` 优先级高于 config 文件（见 `config.ts`）。

---

## 四、验证

```bash
# gateway 健康
curl http://127.0.0.1:19000/api/health        # {"ok":true,...}

# speech 用了 piper
curl http://127.0.0.1:19001/health            # {"ok":true,"asr":"mock","tts":"piper"}
curl -X POST http://127.0.0.1:19001/tts/speak -H 'Content-Type: application/json' \
     -d '{"text":"好的，前进","style":"ok"}'   # 返回 audioBase64 非空、engine=piper

# gateway 能否连到真车 jarvis（先配对拿 token，略；或直接看 jarvis）
curl http://127.0.0.1:8080/api/state           # 换成真车 jarvis 地址
```

手机/平板连厂 WiFi，浏览器访问 `http://<车IP>:19000/` → 配对 → 现场解锁 → 输入/语音下发指令。

生成车身配对二维码：

```bash
cd /usr/local/forkai
.node20/bin/node deploy/scripts/gen-vehicle-qr.mjs --host <车IP> --port 19000 --id fork-01
```

---

## 五、常见问题（FAQ）

- **服务起不来 / 找不到 node**：确认 systemd 单元里 `PATH` 含 `/usr/local/forkai/.node20/bin`，且该目录 `node -v` 正常。看日志 `journalctl -u forkai-gateway -f`。
- **TTS 是英文/机械音**：speech 没加载到 piper（回退 mock 了）。检查 `services/speech/piper/piper/piper` 可执行、模型 onnx 存在、架构与工控机匹配。`curl http://127.0.0.1:19001/health` 应显示 `tts:piper`。
- **gateway 报 502 / 连不上 jarvis**：`jarvis.baseUrl` 端口不对，或真车 jarvis web 服务没起。用 `curl <jarvis>/api/state` 直连测。
- **浏览器不出声**：先点一下页面（解除 autoplay 限制）；确认前端控制台 `[forkai] 走 piper 音频播放`。
- **车端无网**：需在有网机器先跑 `install.sh` 下载好 Node/piper/依赖，再把整个 `/usr/local/forkai`（含 `.node20`、`services/speech/piper`、`node_modules`）打包拷到车上，只补 systemd 步骤。

---

## 安全

- 厂网内须配对码才能控制；点动须现场码。
- 语音「停」≠ 实体急停，急停仍以车身硬件为准。
- 勿将 gateway（19000）暴露到公网。
