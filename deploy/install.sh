#!/usr/bin/env bash
#
# forkAI 一键部署脚本 V2（车载工控机，统一后端架构）
# 组件：forkai-core（FastAPI 统一后端 :19000）+ forkai-llm（llama.cpp 侧车 :19002）
# 替代 V1 的 forkai-gateway + forkai-speech（本脚本会自动移除旧单元）
#
# 支持架构：x86_64 / aarch64（armv7l 不支持：sherpa-onnx 无 32 位 wheel）
# 目标系统：Ubuntu 20.04（glibc 2.31——预编译 llama.cpp 不可用，一律源码编译）
# 安装到 /usr/local/forkai，配置 systemd 并 enable --now
#
# 用法：
#   sudo bash deploy/install.sh                 # 完整安装
#   sudo JARVIS_BASE_URL=http://127.0.0.1:10000 bash deploy/install.sh   # 指定真车 jarvis 地址
#
# 无网模式：先把离线包放到 $INSTALL_DIR/.offline-assets/（目录结构见 deploy/README.md），
# 存在该目录时全部下载改为本地拷贝。
#
set -euo pipefail

# ---------- 可配参数（环境变量覆盖）----------
INSTALL_DIR="${INSTALL_DIR:-/usr/local/forkai}"
NODE_VER="${NODE_VER:-v20.18.1}"
PIPER_REL="${PIPER_REL:-2023.11.14-2}"
PIPER_MODEL="${PIPER_MODEL:-zh_CN-huayan-medium}"
ASR_MODEL="${ASR_MODEL:-sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23}"
LLM_GGUF="${LLM_GGUF:-qwen2-0_5b-instruct-q4_k_m.gguf}"
LLAMA_VER="${LLAMA_VER:-b10256}"
JARVIS_BASE_URL="${JARVIS_BASE_URL:-http://127.0.0.1:8080}"   # 真车 jarvis web 地址，按实际改
FORKAI_PORT="${FORKAI_PORT:-19000}"
LLM_PORT="${LLM_PORT:-19002}"
VEHICLE_ID="${VEHICLE_ID:-fork-01}"
TTS_API_KEY="${TTS_API_KEY:-}"
DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"
PIP_INDEX="${PIP_INDEX:-https://mirrors.aliyun.com/pypi/simple/}"  # 阿里云镜像加速，可改

log()  { echo -e "\033[1;32m[install]\033[0m $*"; }
warn() { echo -e "\033[1;33m[install][warn]\033[0m $*"; }
die()  { echo -e "\033[1;31m[install][error]\033[0m $*" >&2; exit 1; }

# ---------- 0. 前置检查 ----------
[ "$(id -u)" -eq 0 ] || die "请用 root 或 sudo 运行（要写入 $INSTALL_DIR 和 /etc/systemd/system）"
command -v curl >/dev/null || die "缺少 curl，请先安装"
command -v tar  >/dev/null || die "缺少 tar，请先安装"

UNAME_M="$(uname -m)"
case "$UNAME_M" in
  x86_64)        NODE_ARCH="x64";    PIPER_ARCH="x86_64";  CONDA_ARCH="x86_64" ;;
  aarch64|arm64) NODE_ARCH="arm64";  PIPER_ARCH="aarch64"; CONDA_ARCH="aarch64" ;;
  armv7l|armhf)  die "不支持 armv7l：sherpa-onnx 无 32 位 wheel，无法运行 ASR" ;;
  *) die "不支持的架构: $UNAME_M（仅支持 x86_64 / aarch64）" ;;
esac
log "检测到架构: $UNAME_M -> Node=linux-$NODE_ARCH, piper=linux_$PIPER_ARCH, conda=Linux-$CONDA_ARCH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
log "源码目录: $SRC_ROOT"
[ -f "$SRC_ROOT/package.json" ] || die "未找到仓库根 package.json，请从仓库根目录的 deploy/ 运行本脚本"

OFFLINE_DIR="$INSTALL_DIR/.offline-assets"
if [ -d "$OFFLINE_DIR" ]; then
  log "检测到离线包目录 $OFFLINE_DIR，全部下载改为本地拷贝"
fi

# fetch <dest> <offline文件名> <url1> [备选url2]
fetch() {
  local dest="$1" name="$2" url1="$3" url2="${4:-}"
  if [ -f "$OFFLINE_DIR/$name" ]; then
    log "离线包: $name"
    cp "$OFFLINE_DIR/$name" "$dest"
    return 0
  fi
  curl -fSL --retry 3 -o "$dest" "$url1" && return 0
  [ -n "$url2" ] || die "下载失败: $url1"
  warn "主源失败，换镜像: $url2"
  curl -fSL --retry 3 -o "$dest" "$url2"
}

# ---------- 1. 同步代码到安装目录 ----------
log "同步代码到 $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
# 排除运行期/构建产物/模型大文件（模型由本脚本统一下载/离线拷贝）
EXCLUDES=(
  --exclude 'node_modules' --exclude '**/node_modules'
  --exclude '.node20' --exclude '.miniconda' --exclude '.git'
  --exclude 'services/speech/piper'
  --exclude 'services/core/models/piper' --exclude 'services/core/models/asr'
  --exclude 'services/core/assets'
  --exclude 'services/core/models/llm' --exclude 'services/core/.venv'
  --exclude 'services/llm-sidecar/src' --exclude 'services/llm-sidecar/bin'
  --exclude 'dist' --exclude '**/dist'
)
if command -v rsync >/dev/null; then
  rsync -a --delete "${EXCLUDES[@]}" "$SRC_ROOT/" "$INSTALL_DIR/"
else
  warn "无 rsync，用 tar 拷贝（略慢）"
  tar -C "$SRC_ROOT" \
    --exclude='./node_modules' --exclude='./**/node_modules' \
    --exclude='./.node20' --exclude='./.miniconda' --exclude='./.git' \
    --exclude='./services/speech/piper' \
    --exclude='./services/core/models/piper' --exclude='./services/core/models/asr' \
    --exclude='./services/core/assets' \
    --exclude='./services/core/models/llm' --exclude='./services/core/.venv' \
    --exclude='./services/llm-sidecar/src' --exclude='./services/llm-sidecar/bin' \
    --exclude='./dist' --exclude='./**/dist' \
    -cf - . | tar -C "$INSTALL_DIR" -xf -
fi

NODE_DIR="$INSTALL_DIR/.node20"
CONDA_DIR="$INSTALL_DIR/.miniconda"
CORE_DIR="$INSTALL_DIR/services/core"
export PATH="$NODE_DIR/bin:$PATH"

# ---------- 2. 安装 Node 20（项目内，前端构建需要）----------
if [ -x "$NODE_DIR/bin/node" ] && [ "$("$NODE_DIR/bin/node" -v)" = "$NODE_VER" ]; then
  log "Node $NODE_VER 已存在，跳过"
else
  log "下载 Node $NODE_VER (linux-$NODE_ARCH) ..."
  NODE_TARBALL="node-$NODE_VER-linux-$NODE_ARCH.tar.xz"
  fetch "/tmp/$NODE_TARBALL" "$NODE_TARBALL" "https://nodejs.org/dist/$NODE_VER/$NODE_TARBALL"
  rm -rf "$NODE_DIR"; mkdir -p "$NODE_DIR"
  tar -xJf "/tmp/$NODE_TARBALL" -C "$NODE_DIR" --strip-components=1
  rm -f "/tmp/$NODE_TARBALL"
fi
log "Node 版本: $("$NODE_DIR/bin/node" -v)  npm: $("$NODE_DIR/bin/npm" -v)"

# ---------- 3. Python 环境（Miniconda 项目内安装，不污染系统）----------
# Ubuntu 20.04 系统 Python 为 3.8（core 需要 >=3.10），Miniconda 提供 3.13 且
# 同时满足 aarch64（RK3588）——比源码编译 Python 快得多，也比 deadsnakes 适合 ARM。
if [ -x "$CONDA_DIR/bin/python3" ]; then
  log "Miniconda 已存在，跳过"
else
  log "下载 Miniconda3 (Linux-$CONDA_ARCH) ..."
  CONDA_SH="Miniconda3-latest-Linux-$CONDA_ARCH.sh"
  fetch "/tmp/$CONDA_SH" "$CONDA_SH" \
    "https://repo.anaconda.com/miniconda/$CONDA_SH" \
    "https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/$CONDA_SH"
  bash "/tmp/$CONDA_SH" -b -p "$CONDA_DIR"
  rm -f "/tmp/$CONDA_SH"
fi
log "Python: $("$CONDA_DIR/bin/python3" --version)"

if [ -x "$CORE_DIR/.venv/bin/python" ]; then
  log "core venv 已存在，跳过创建"
else
  log "创建 services/core/.venv ..."
  "$CONDA_DIR/bin/python3" -m venv "$CORE_DIR/.venv"
fi
log "pip install -r requirements.txt（镜像: $PIP_INDEX）..."
"$CORE_DIR/.venv/bin/pip" install --upgrade pip -i "$PIP_INDEX"
"$CORE_DIR/.venv/bin/pip" install -i "$PIP_INDEX" -r "$CORE_DIR/requirements.txt"

# ---------- 4. 安装依赖并构建前端 ----------
if [ -d "$INSTALL_DIR/node_modules" ]; then
  log "node_modules 已存在，跳过 npm install（离线/复用场景）"
else
  log "npm install（--legacy-peer-deps）..."
  cd "$INSTALL_DIR"
  "$NODE_DIR/bin/npm" install --legacy-peer-deps --no-audit --no-fund
fi
log "构建前端 apps/web -> dist ..."
"$NODE_DIR/bin/npm" run build -w @forkai/web

# ---------- 5. piper（TTS，core 内置调用；模型放 services/core/models/piper/）----------
PIPER_DIR="$CORE_DIR/models/piper"
mkdir -p "$PIPER_DIR"
if [ -x "$PIPER_DIR/piper/piper" ]; then
  log "piper 已存在，跳过下载二进制"
else
  log "下载 piper $PIPER_REL (linux_$PIPER_ARCH) ..."
  fetch /tmp/piper.tar.gz "piper_linux_$PIPER_ARCH.tar.gz" \
    "https://github.com/rhasspy/piper/releases/download/$PIPER_REL/piper_linux_$PIPER_ARCH.tar.gz"
  tar -xzf /tmp/piper.tar.gz -C "$PIPER_DIR"
  rm -f /tmp/piper.tar.gz
fi
if [ -f "$PIPER_DIR/$PIPER_MODEL.onnx" ] && [ -f "$PIPER_DIR/$PIPER_MODEL.onnx.json" ]; then
  log "中文模型 $PIPER_MODEL 已存在，跳过"
else
  log "下载中文模型 $PIPER_MODEL ..."
  fetch "$PIPER_DIR/$PIPER_MODEL.onnx" "$PIPER_MODEL.onnx" \
    "https://huggingface.co/csukuangfj/vits-piper-$PIPER_MODEL/resolve/main/$PIPER_MODEL.onnx" \
    "https://hf-mirror.com/csukuangfj/vits-piper-$PIPER_MODEL/resolve/main/$PIPER_MODEL.onnx"
  fetch "$PIPER_DIR/$PIPER_MODEL.onnx.json" "$PIPER_MODEL.onnx.json" \
    "https://huggingface.co/csukuangfj/vits-piper-$PIPER_MODEL/resolve/main/$PIPER_MODEL.onnx.json" \
    "https://hf-mirror.com/csukuangfj/vits-piper-$PIPER_MODEL/resolve/main/$PIPER_MODEL.onnx.json"
fi
log "验证 piper 可运行 ..."
if echo "测试" | LD_LIBRARY_PATH="$PIPER_DIR/piper:${LD_LIBRARY_PATH:-}" \
    "$PIPER_DIR/piper/piper" --model "$PIPER_DIR/$PIPER_MODEL.onnx" \
    --config "$PIPER_DIR/$PIPER_MODEL.onnx.json" \
    --espeak_data "$PIPER_DIR/piper/espeak-ng-data" \
    --output_file /tmp/forkai_piper_check.wav >/dev/null 2>&1; then
  log "piper 合成 OK"
  rm -f /tmp/forkai_piper_check.wav
else
  warn "piper 合成自检失败（架构不匹配或缺依赖），TTS 将回退 mock"
fi

# ---------- 6. ASR 模型（sherpa-onnx 流式 zipformer 中文 int8）----------
ASR_DIR="$CORE_DIR/models/asr"
mkdir -p "$ASR_DIR"
if [ -f "$ASR_DIR/$ASR_MODEL/tokens.txt" ]; then
  log "ASR 模型已存在，跳过"
else
  log "下载 ASR 模型 $ASR_MODEL（约 78M）..."
  fetch /tmp/asr-model.tar.bz2 "$ASR_MODEL.tar.bz2" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$ASR_MODEL.tar.bz2" \
    "https://hf-mirror.com/csukuangfj/$ASR_MODEL/resolve/main/$ASR_MODEL.tar.bz2"
  tar -xjf /tmp/asr-model.tar.bz2 -C "$ASR_DIR"
  rm -f /tmp/asr-model.tar.bz2
fi
# 指令词热词表（仓库内维护，随代码同步被排除 models/，单独拷贝）
if [ -f "$SRC_ROOT/services/core/models/asr/hotwords.txt" ]; then
  cp "$SRC_ROOT/services/core/models/asr/hotwords.txt" "$ASR_DIR/hotwords.txt"
fi

# ---------- 7. LLM（Qwen2-0.5B GGUF + llama.cpp 源码编译）----------
LLM_DIR="$CORE_DIR/models/llm"
mkdir -p "$LLM_DIR"
if [ -f "$LLM_DIR/$LLM_GGUF" ]; then
  log "LLM 模型已存在，跳过"
else
  log "下载 LLM 模型 $LLM_GGUF（约 380M，优先 ModelScope 镜像）..."
  # 备选：https://hf-mirror.com/Qwen/Qwen2-0.5B-Instruct-GGUF/resolve/main/$LLM_GGUF
  #       https://huggingface.co/Qwen/Qwen2-0.5B-Instruct-GGUF/resolve/main/$LLM_GGUF
  fetch "$LLM_DIR/$LLM_GGUF" "$LLM_GGUF" \
    "https://modelscope.cn/models/Qwen/Qwen2-0.5B-Instruct-GGUF/resolve/master/$LLM_GGUF" \
    "https://hf-mirror.com/Qwen/Qwen2-0.5B-Instruct-GGUF/resolve/main/$LLM_GGUF"
fi

SIDECAR_DIR="$INSTALL_DIR/services/llm-sidecar"
mkdir -p "$SIDECAR_DIR/bin"
if [ -x "$SIDECAR_DIR/bin/llama-server" ]; then
  log "llama-server 已存在，跳过编译"
else
  log "编译 llama.cpp $LLAMA_VER（RK3588 约 15-25 分钟，请耐心等待）..."
  apt-get update -qq
  apt-get install -y -qq build-essential cmake ninja-build git ca-certificates
  LLAMA_SRC_TARBALL="llama.cpp-$LLAMA_VER.tar.gz"
  fetch "/tmp/$LLAMA_SRC_TARBALL" "$LLAMA_SRC_TARBALL" \
    "https://github.com/ggml-org/llama.cpp/archive/refs/tags/$LLAMA_VER.tar.gz"
  rm -rf "$SIDECAR_DIR/src"
  mkdir -p "$SIDECAR_DIR/src"
  tar -xzf "/tmp/$LLAMA_SRC_TARBALL" -C "$SIDECAR_DIR/src" --strip-components=1
  rm -f "/tmp/$LLAMA_SRC_TARBALL"
  # GGML_NATIVE=OFF 保证可移植（不针对本机 CPU 特化）；
  # LLAMA_CURL=OFF 去掉 libcurl/libssl3 依赖（Ubuntu 20.04 无 libssl3）；
  # BUILD_SHARED_LIBS=OFF 静态链接单二进制，拷贝即用
  cmake -S "$SIDECAR_DIR/src" -B "$SIDECAR_DIR/src/build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF -DLLAMA_CURL=OFF -DBUILD_SHARED_LIBS=OFF
  cmake --build "$SIDECAR_DIR/src/build" --target llama-server -j"$(nproc)"
  cp "$SIDECAR_DIR/src/build/bin/llama-server" "$SIDECAR_DIR/bin/llama-server"
  log "llama-server 编译完成: $SIDECAR_DIR/bin/llama-server"
fi

# ---------- 8. 写 core 配置（环境变量注入）----------
CORE_CFG="$CORE_DIR/config/core.config.yaml"
log "写入 core 配置（jarvis.baseUrl=$JARVIS_BASE_URL port=$FORKAI_PORT）..."
cat > "$CORE_CFG" <<EOF
jarvis:
  baseUrl: $JARVIS_BASE_URL   # 真车 jarvis web 地址；改后 systemctl restart forkai-core
server:
  host: 0.0.0.0
  port: $FORKAI_PORT
vehicleId: $VEHICLE_ID
watchdogMs: 2000
siteSessionTtlMs: 2700000
pairCodeTtlMs: 300000
pairTokenTtlMs: 86400000
wakeArmMs: 30000
speed:
  default: 20
  max: 40
  step: 5
wakeWords:
  - 玖物玖物
  - 玖物，玖物
  - 九物九物
speak:
  cabinMove: vehicle
  pttMove: both
  query: device
tts:
  cloud:
    enabled: true
    base_url: https://api.siliconflow.cn/v1
    model: FunAudioLLM/CosyVoice2-0.5B
    voice: anna
    timeout_s: 1.5
    api_key_env: FORKAI_TTS_API_KEY
  cache_dir: data/tts_cache
  cache_max_files: 5000
  prewarm_on_startup: true
piper:
  bin: models/piper/piper/piper
  libDir: models/piper/piper
  model: models/piper/$PIPER_MODEL.onnx
  config: models/piper/$PIPER_MODEL.onnx.json
asr:
  sample_rate: 16000
  cloud:
    enabled: true
    base_url: https://api.siliconflow.cn/v1
    model: Qwen/Qwen3-ASR-1.7B
    timeout_s: 5
    api_key_env: FORKAI_TTS_API_KEY
  model_dir: models/asr/$ASR_MODEL
  num_threads: 1
  enable_endpoint: true
  rule1_min_trailing_silence: 2.4
  rule2_min_trailing_silence: 1.2
  rule3_min_utterance_length: 20.0
cabin_listen:
  enabled: false
  device: null
fork:
  min_pos: 75
  max_pos: 210
  step: 50
  wait: 20
  tolerance: 20
  confirm_threshold: 100
llm:
  enabled: true
  timeout_s: 10
  model: deepseek-ai/DeepSeek-V3.2
  siliconflow:
    base_url: https://api.siliconflow.cn/v1
    api_key_env: FORKAI_TTS_API_KEY
  deepseek:
    base_url: https://api.deepseek.com/v1
    model: deepseek-chat
    api_key_env: FORKAI_DEEPSEEK_API_KEY
taskflow:
  node_timeout_s: 120
  low_battery_pct: 20
  resume_battery_pct: 80
  poll_ms: 500
safety:
  estop_exit_site: true
EOF

# ---------- 9. systemd 双单元（替代旧 gateway/speech）----------
log "安装 systemd 单元 ..."
if systemctl list-unit-files 2>/dev/null | grep -qE 'forkai-(gateway|speech)'; then
  log "检测到旧单元 forkai-gateway/forkai-speech，停用并移除（V1 → V2 迁移）"
  systemctl stop forkai-gateway forkai-speech 2>/dev/null || true
  systemctl disable forkai-gateway forkai-speech 2>/dev/null || true
  rm -f /etc/systemd/system/forkai-gateway.service /etc/systemd/system/forkai-speech.service
fi

cat > /etc/systemd/system/forkai-llm.service <<EOF
[Unit]
Description=forkAI llm sidecar (llama.cpp llama-server, Qwen2)
After=network.target

[Service]
Type=simple
WorkingDirectory=$SIDECAR_DIR
Environment=LLM_PORT=$LLM_PORT
ExecStart=$SIDECAR_DIR/run-llama-server.sh
Restart=always
RestartSec=3
MemoryMax=2G

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/forkai-core.service <<EOF
[Unit]
Description=forkAI core (FastAPI unified backend)
After=network.target

[Service]
Type=simple
WorkingDirectory=$CORE_DIR
Environment=JARVIS_BASE_URL=$JARVIS_BASE_URL
Environment=FORKAI_PORT=$FORKAI_PORT
Environment=VEHICLE_ID=$VEHICLE_ID
Environment=FORKAI_TTS_API_KEY=$TTS_API_KEY
Environment=FORKAI_DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY
ExecStart=$CORE_DIR/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $FORKAI_PORT
Restart=always
RestartSec=2
MemoryMax=4G

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now forkai-core

# ---------- 10. 完成提示 ----------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
log "================ 部署完成（V2）================"
log "core : http://0.0.0.0:$FORKAI_PORT  (代理 jarvis: $JARVIS_BASE_URL)"
log "llm  : http://127.0.0.1:$LLM_PORT  (仅本机)"
log "访问入口 : http://${IP:-<车IP>}:$FORKAI_PORT/   (手机/平板连厂 WiFi)"
log ""
log "验证清单:"
log "  curl http://127.0.0.1:$FORKAI_PORT/api/health     # {\"ok\":true,...}"
log "  curl http://127.0.0.1:$LLM_PORT/health            # {\"status\":\"ok\"}"
log "  systemctl status forkai-core forkai-llm"
log "  journalctl -u forkai-core -f"
log "  ASR 自检: cd $CORE_DIR && .venv/bin/python scripts/asr_offline_test.py"
log "  改 jarvis 端口: 编辑 $CORE_CFG 的 jarvis.baseUrl，然后 systemctl restart forkai-core"
log "  生成配对二维码: cd $INSTALL_DIR && $NODE_DIR/bin/node deploy/scripts/gen-vehicle-qr.mjs --host ${IP:-<车IP>} --port $FORKAI_PORT --id $VEHICLE_ID"
echo
systemctl --no-pager --full status forkai-core forkai-llm 2>/dev/null | grep -E "●|Active:" || true
