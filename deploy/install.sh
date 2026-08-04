#!/usr/bin/env bash
#
# forkAI 一键部署脚本（车载工控机）
# 支持架构：x86_64 / aarch64 / armv7l
# 安装到 /usr/local/forkai，配置 systemd 并 enable --now（开机自启 + 立即启动）
#
# 用法：
#   sudo bash deploy/install.sh                 # 完整安装
#   sudo JARVIS_BASE_URL=http://127.0.0.1:10000 bash deploy/install.sh   # 指定真车 jarvis 地址
#
set -euo pipefail

# ---------- 可配参数（环境变量覆盖）----------
INSTALL_DIR="${INSTALL_DIR:-/usr/local/forkai}"
NODE_VER="${NODE_VER:-v20.18.1}"
PIPER_REL="${PIPER_REL:-2023.11.14-2}"
PIPER_MODEL="${PIPER_MODEL:-zh_CN-huayan-medium}"
JARVIS_BASE_URL="${JARVIS_BASE_URL:-http://127.0.0.1:8080}"   # 真车 jarvis web 地址，按实际改
SPEECH_PORT="${SPEECH_PORT:-19001}"
FORKAI_PORT="${FORKAI_PORT:-19000}"
VEHICLE_ID="${VEHICLE_ID:-fork-01}"

log()  { echo -e "\033[1;32m[install]\033[0m $*"; }
warn() { echo -e "\033[1;33m[install][warn]\033[0m $*"; }
die()  { echo -e "\033[1;31m[install][error]\033[0m $*" >&2; exit 1; }

# ---------- 0. 前置检查 ----------
[ "$(id -u)" -eq 0 ] || die "请用 root 或 sudo 运行（要写入 $INSTALL_DIR 和 /etc/systemd/system）"
command -v curl >/dev/null || die "缺少 curl，请先安装"
command -v tar  >/dev/null || die "缺少 tar，请先安装"

# 架构映射：uname -m -> Node 架构后缀 / piper 架构后缀
UNAME_M="$(uname -m)"
case "$UNAME_M" in
  x86_64)        NODE_ARCH="x64";    PIPER_ARCH="x86_64"  ;;
  aarch64|arm64) NODE_ARCH="arm64";  PIPER_ARCH="aarch64" ;;
  armv7l|armhf)  NODE_ARCH="armv7l"; PIPER_ARCH="armv7l"  ;;
  *) die "不支持的架构: $UNAME_M（仅支持 x86_64 / aarch64 / armv7l）" ;;
esac
log "检测到架构: $UNAME_M  ->  Node=linux-$NODE_ARCH, piper=linux_$PIPER_ARCH"

# 源码目录 = 脚本所在仓库根目录（deploy/ 的上一级）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
log "源码目录: $SRC_ROOT"
[ -f "$SRC_ROOT/package.json" ] || die "未找到仓库根 package.json，请从仓库根目录的 deploy/ 运行本脚本"

# ---------- 1. 同步代码到安装目录 ----------
log "同步代码到 $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
# 排除运行期/构建产物与大文件，避免把本机 node_modules、.node20、piper 拷过去
if command -v rsync >/dev/null; then
  rsync -a --delete \
    --exclude 'node_modules' --exclude '**/node_modules' \
    --exclude '.node20' --exclude '.git' \
    --exclude 'services/speech/piper' \
    --exclude 'dist' --exclude '**/dist' \
    "$SRC_ROOT/" "$INSTALL_DIR/"
else
  warn "无 rsync，用 tar 拷贝（略慢）"
  tar -C "$SRC_ROOT" \
    --exclude='./node_modules' --exclude='./**/node_modules' \
    --exclude='./.node20' --exclude='./.git' \
    --exclude='./services/speech/piper' \
    --exclude='./dist' --exclude='./**/dist' \
    -cf - . | tar -C "$INSTALL_DIR" -xf -
fi

NODE_DIR="$INSTALL_DIR/.node20"
export PATH="$NODE_DIR/bin:$PATH"

# ---------- 2. 安装 Node 20（项目内，不污染系统）----------
if [ -x "$NODE_DIR/bin/node" ] && [ "$("$NODE_DIR/bin/node" -v)" = "$NODE_VER" ]; then
  log "Node $NODE_VER 已存在，跳过"
else
  log "下载 Node $NODE_VER (linux-$NODE_ARCH) ..."
  NODE_TARBALL="node-$NODE_VER-linux-$NODE_ARCH.tar.xz"
  curl -fSL --retry 3 -o "/tmp/$NODE_TARBALL" "https://nodejs.org/dist/$NODE_VER/$NODE_TARBALL"
  rm -rf "$NODE_DIR"
  mkdir -p "$NODE_DIR"
  tar -xJf "/tmp/$NODE_TARBALL" -C "$NODE_DIR" --strip-components=1
  rm -f "/tmp/$NODE_TARBALL"
fi
log "Node 版本: $("$NODE_DIR/bin/node" -v)  npm: $("$NODE_DIR/bin/npm" -v)"

# ---------- 3. 安装依赖并构建前端 ----------
log "npm install（--legacy-peer-deps）..."
cd "$INSTALL_DIR"
"$NODE_DIR/bin/npm" install --legacy-peer-deps --no-audit --no-fund

log "构建前端 apps/web -> dist ..."
"$NODE_DIR/bin/npm" run build -w @forkai/web

# ---------- 4. 安装 piper（按架构）+ 中文模型 ----------
PIPER_DIR="$INSTALL_DIR/services/speech/piper"
mkdir -p "$PIPER_DIR"
if [ -x "$PIPER_DIR/piper/piper" ]; then
  log "piper 已存在，跳过下载二进制"
else
  log "下载 piper $PIPER_REL (linux_$PIPER_ARCH) ..."
  curl -fSL --retry 3 -o /tmp/piper.tar.gz \
    "https://github.com/rhasspy/piper/releases/download/$PIPER_REL/piper_linux_$PIPER_ARCH.tar.gz"
  tar -xzf /tmp/piper.tar.gz -C "$PIPER_DIR"
  rm -f /tmp/piper.tar.gz
fi

if [ -f "$PIPER_DIR/$PIPER_MODEL.onnx" ] && [ -f "$PIPER_DIR/$PIPER_MODEL.onnx.json" ]; then
  log "中文模型 $PIPER_MODEL 已存在，跳过"
else
  log "下载中文模型 $PIPER_MODEL ..."
  curl -fSL --retry 3 -o "$PIPER_DIR/$PIPER_MODEL.onnx" \
    "https://huggingface.co/csukuangfj/vits-piper-$PIPER_MODEL/resolve/main/$PIPER_MODEL.onnx"
  curl -fSL --retry 3 -o "$PIPER_DIR/$PIPER_MODEL.onnx.json" \
    "https://huggingface.co/csukuangfj/vits-piper-$PIPER_MODEL/resolve/main/$PIPER_MODEL.onnx.json"
fi

# 冒烟：piper 能否在本机架构上合成
log "验证 piper 可运行 ..."
if echo "测试" | LD_LIBRARY_PATH="$PIPER_DIR/piper:${LD_LIBRARY_PATH:-}" \
    "$PIPER_DIR/piper/piper" --model "$PIPER_DIR/$PIPER_MODEL.onnx" \
    --config "$PIPER_DIR/$PIPER_MODEL.onnx.json" \
    --espeak_data "$PIPER_DIR/piper/espeak-ng-data" \
    --output_file /tmp/forkai_piper_check.wav >/dev/null 2>&1; then
  log "piper 合成 OK"
  rm -f /tmp/forkai_piper_check.wav
else
  warn "piper 合成自检失败（可能是架构不匹配或缺依赖），speech 将回退 mock。请检查架构是否正确。"
fi

# ---------- 5. 生成 gateway 配置（指向真车 jarvis）----------
GW_CFG="$INSTALL_DIR/services/voice-gateway/config/gateway.config.yaml"
log "写入 gateway 配置（jarvis.baseUrl=$JARVIS_BASE_URL）..."
cat > "$GW_CFG" <<EOF
jarvis:
  baseUrl: $JARVIS_BASE_URL   # 真车 jarvis web 地址，按实际端口修改后 systemctl restart forkai-gateway
speech:
  baseUrl: http://127.0.0.1:$SPEECH_PORT
server:
  host: 0.0.0.0
  port: $FORKAI_PORT
vehicleId: $VEHICLE_ID
watchdogMs: 2000
siteSessionTtlMs: 2700000
pairCodeTtlMs: 300000
pairTokenTtlMs: 86400000
wakeArmMs: 12000
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
EOF

# ---------- 6. 安装 systemd 单元并 enable --now ----------
log "安装 systemd 单元 ..."
NPM_BIN="$NODE_DIR/bin/npm"
cat > /etc/systemd/system/forkai-speech.service <<EOF
[Unit]
Description=forkAI speech (piper offline TTS / ASR adapter)
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
Environment=PATH=$NODE_DIR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=SPEECH_PORT=$SPEECH_PORT
ExecStart=$NPM_BIN run start -w @forkai/speech
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/forkai-gateway.service <<EOF
[Unit]
Description=forkAI voice gateway
After=network.target forkai-speech.service
Wants=forkai-speech.service

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
Environment=PATH=$NODE_DIR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=JARVIS_BASE_URL=$JARVIS_BASE_URL
Environment=SPEECH_BASE_URL=http://127.0.0.1:$SPEECH_PORT
Environment=FORKAI_PORT=$FORKAI_PORT
Environment=VEHICLE_ID=$VEHICLE_ID
ExecStart=$NPM_BIN run start -w @forkai/voice-gateway
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now forkai-speech forkai-gateway

# ---------- 7. 完成提示 ----------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
log "================ 部署完成 ================"
log "speech   : http://127.0.0.1:$SPEECH_PORT  (tts=$( [ -x "$PIPER_DIR/piper/piper" ] && echo piper || echo mock ))"
log "gateway  : http://0.0.0.0:$FORKAI_PORT  (代理 jarvis: $JARVIS_BASE_URL)"
log "访问入口 : http://${IP:-<车IP>}:$FORKAI_PORT/   (手机/平板连厂 WiFi)"
log ""
log "常用命令:"
log "  查看状态   systemctl status forkai-gateway forkai-speech"
log "  看日志     journalctl -u forkai-gateway -f"
log "  改 jarvis 端口后重启: 编辑 $GW_CFG 的 jarvis.baseUrl，然后 systemctl restart forkai-gateway"
log "  生成配对二维码: cd $INSTALL_DIR && $NODE_DIR/bin/node deploy/scripts/gen-vehicle-qr.mjs --host ${IP:-<车IP>} --port $FORKAI_PORT --id $VEHICLE_ID"
echo
systemctl --no-pager --full status forkai-gateway forkai-speech 2>/dev/null | grep -E "●|Active:" || true
