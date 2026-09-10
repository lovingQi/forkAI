# forkAI 车载部署说明（V2）

把 forkAI 部署到车载工控机，对接真车 jarvis。**V2 组件变化**：统一后端 `forkai-core`（FastAPI，含 ASR/NLU/TTS/任务流）+ `forkai-llm`（llama.cpp 侧车），替代 V1 的 `forkai-gateway` + `forkai-speech`（install.sh 会自动停用并删除旧单元）。

- 支持架构：**x86_64 / aarch64**（armv7l 不支持：sherpa-onnx 无 32 位 wheel）
- 目标系统：Ubuntu 20.04（glibc 2.31：llama.cpp 一律源码编译，不用预编译二进制）
- 安装路径：`/usr/local/forkai`
- 端口：`19000` forkai-core（外部访问入口）；`19002` forkai-llm（仅本机回环）
- 真车 jarvis 由车端自身提供（HTTP `/api/*` + WS `/ws/high|low`），core 只做代理，**不需要 mock-jarvis**

---

## 一、一键部署（推荐）

把整个仓库拷到工控机，然后：

```bash
cd /path/to/forkAI
sudo bash deploy/install.sh
```

指定真车 jarvis 地址（默认 `http://127.0.0.1:8080`）：

```bash
sudo JARVIS_BASE_URL=http://127.0.0.1:10000 bash deploy/install.sh
```

脚本自动完成：架构判断 → 装 Node20（项目内）→ 装 Miniconda + 建 venv + pip 依赖 → npm install + 构建前端 → piper+中文模型 → ASR 模型+热词 → Qwen2 GGUF + 源码编译 llama.cpp（**RK3588 约 15-25 分钟，请耐心等待**）→ 写 core.config.yaml → 装 systemd 双单元并 `enable --now`。

可用环境变量覆盖：

| 变量 | 默认 | 说明 |
|---|---|---|
| `INSTALL_DIR` | `/usr/local/forkai` | 安装目录 |
| `JARVIS_BASE_URL` | `http://127.0.0.1:8080` | 真车 jarvis web 地址 |
| `FORKAI_PORT` | `19000` | core 端口（外部访问入口） |
| `LLM_PORT` | `19002` | llm 侧车端口（仅本机） |
| `VEHICLE_ID` | `fork-01` | 车辆编号 |
| `TTS_API_KEY` | 空 | 写入 systemd `FORKAI_TTS_API_KEY`（SiliconFlow，TTS/ASR/云端 LLM）；空则 TTS 回退 piper、ASR 报识别失败 |
| `DEEPSEEK_API_KEY` | 空 | 写入 systemd `FORKAI_DEEPSEEK_API_KEY`（官方 DeepSeek-flash） |
| `NODE_VER` | `v20.18.1` | Node 版本（前端构建用） |
| `PIP_INDEX` | 阿里云镜像 | pip 源，可改 |
| `LLAMA_VER` | `b10256` | llama.cpp 版本 |

---

## 二、无网离线部署

在车上有网前，先把离线包放进 `$INSTALL_DIR/.offline-assets/`（脚本检测到该目录即全部用本地拷贝，跳过下载）：

| 文件 | 来源 |
|------|------|
| `node-v20.18.1-linux-arm64.tar.xz` | nodejs.org/dist |
| `Miniconda3-latest-Linux-aarch64.sh` | repo.anaconda.com / 清华镜像 |
| `piper_linux_aarch64.tar.gz` | github.com/rhasspy/piper releases |
| `zh_CN-huayan-medium.onnx` / `.onnx.json` | huggingface csukuangfj（hf-mirror 备选） |
| `sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23.tar.bz2` | github k2-fsa releases（hf-mirror 备选） |
| `qwen2-0_5b-instruct-q4_k_m.gguf` | ModelScope（推荐）/ hf-mirror |
| `llama.cpp-b10256.tar.gz` | github ggml-org/llama.cpp 源码包 |

注意：`npm install` 与 `apt-get install build-essential cmake ninja-build git` 仍需有网；完全无网时请在有网机器上预装 node_modules（拷入 `$INSTALL_DIR/node_modules` 即可跳过）并预装编译工具链 deb 包。

---

## 三、配置真车 jarvis 端口

- 改配置：`services/core/config/core.config.yaml` 的 `jarvis.baseUrl`
- 或改 systemd：`/etc/systemd/system/forkai-core.service` 的 `Environment=JARVIS_BASE_URL=...`

改完重启：`sudo systemctl restart forkai-core`（环境变量优先级高于配置文件，见 `app/config.py`）。

---

## 四、验证清单

```bash
# core 健康
curl http://127.0.0.1:19000/api/health      # {"ok":true,"vehicleId":"fork-01",...}

# llm 侧车
curl http://127.0.0.1:19002/health          # {"status":"ok"}

# ASR/TTS 端到端自检（piper 合成 → sherpa 识别，应输出 PASS）
cd /usr/local/forkai/services/core && .venv/bin/python scripts/asr_offline_test.py

# 云端 TTS 缓存链路（需 FORKAI_TTS_API_KEY）
cd /usr/local/forkai/services/core && .venv/bin/python scripts/tts_cloud_test.py

# 服务状态/日志
systemctl status forkai-core forkai-llm
journalctl -u forkai-core -f
```

手机/平板连厂 WiFi，浏览器访问 `http://<车IP>:19000/` → 配对 → 现场解锁 → 语音下发指令。

生成车身配对二维码：

```bash
cd /usr/local/forkai
.node20/bin/node deploy/scripts/gen-vehicle-qr.mjs --host <车IP> --port 19000 --id fork-01
```

---

## 五、常见问题（FAQ）

- **llama.cpp 编译失败**：先确认 `build-essential cmake ninja-build git` 装齐；gcc 需 ≥9（C++17）。内存不足时把 `-j$(nproc)` 改成 `-j2`。日志在终端直接输出；重跑 install.sh 会复用已下载源码包。
- **llama-server 启动失败**：`journalctl -u forkai-llm -f` 看模型路径；GGUF 必须完整（397,805,248 字节），下载中断的文件会报 tensor out of bounds——删掉重下。
- **模型下载慢/失败**：LLM 默认走 ModelScope，备选 hf-mirror（改 install.sh 的 fetch 第二参数已内置）；pip 用阿里云镜像（`PIP_INDEX` 可换）。
- **core 起不来**：看 `journalctl -u forkai-core`；确认 venv 存在（`services/core/.venv/bin/python --version` ≥3.10）。
- **TTS 无声/英文音**：piper 未就绪且云端也不可用时自动回退 mock（前端浏览器朗读）。检查 `services/core/models/piper/piper/piper` 可执行、模型文件齐全。
- **云端 TTS 不可用**：自动回退 piper（声音会变）。配置 Key：安装时 `sudo TTS_API_KEY=sk-... DEEPSEEK_API_KEY=sk-... bash deploy/install.sh`，或在 `/etc/systemd/system/forkai-core.service` 设置 `Environment=FORKAI_TTS_API_KEY=...` 与 `Environment=FORKAI_DEEPSEEK_API_KEY=...` 后 `systemctl daemon-reload && systemctl restart forkai-core`。开发机 `export FORKAI_TTS_API_KEY=... FORKAI_DEEPSEEK_API_KEY=...` 再 `./start-v2.sh`。
- **内存占用预期**：core（含 sherpa ASR + piper）≈1GB；llama-server（Qwen2-0.5B Q4_K_M，ctx 2048）≈0.5GB；合计约 1.5GB，8GB 整机余量充足。systemd 已加 MemoryMax 保护（core 4G / llm 2G）。
- **V1 旧服务残留**：install.sh 会自动 stop/disable 并删除 forkai-gateway、forkai-speech 单元；旧目录 services/voice-gateway、services/speech 保留在仓库中但不再启动。

---

## 安全

- 厂网内须配对码才能控制；点动须现场码；急停触发自动退出现场模式。
- 语音「停」≠ 实体急停，急停仍以车身硬件为准。
- 勿将 core（19000）暴露到公网；llm（19002）只监听回环。
