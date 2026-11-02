#!/bin/bash
# OmniVoice + FlashInfer 服务启动脚本（WSL2 专用）
#
# 用法:
#   bash ~/omnivoice-wsl/start_server.sh                    # 用默认模型路径
#   bash ~/omnivoice-wsl/start_server.sh /path/to/model     # 指定模型
#   OMNIVOICE_MODEL=/path/to/model bash start_server.sh     # 或用环境变量
#
# 起好后服务监听 0.0.0.0:9877，Windows 侧通过 127.0.0.1:9877（WSL2 localhost 转发）
# 或 WSL 内网 IP 访问。
set -u

DIR="$HOME/omnivoice-wsl"
cd "$DIR" || { echo "[FAIL] 找不到 $DIR（先跑 bridge/start-omnivoice-wsl.ps1 初始化）"; exit 1; }

MODEL="${1:-${OMNIVOICE_MODEL:-/mnt/e/llama-models/models/OmniVoice-bf16}}"
# WSL2 系统自带 nvcc 12.8 会干扰 flashinfer 的 CUDA 版本检测 → 必须指向 venv 里的 cu13 版
export CUDA_HOME="$DIR/.venv/lib/python3.12/site-packages/nvidia/cuda_nvcc"

if pgrep -f "demo_flashinfer.py" >/dev/null 2>&1; then
  echo "[OK] OmniVoice 服务已在运行"
  exit 0
fi

if [ ! -f "$DIR/demo_flashinfer.py" ]; then
  echo "[FAIL] 缺少 $DIR/demo_flashinfer.py"
  echo "       从仓库复制: cp <repo>/bridge/omnivoice/demo_flashinfer.py $DIR/"
  exit 1
fi

if [ ! -x "$DIR/.venv/bin/python" ]; then
  echo "[FAIL] 缺少 $DIR/.venv（按 README「TTS 引擎：OmniVoice」一节用 uv 建环境）"
  exit 1
fi

nohup "$DIR/.venv/bin/python" demo_flashinfer.py \
  --model "$MODEL" \
  --port 9877 --no-asr --ip 0.0.0.0 \
  --graph-buckets 5,10,15,20,30 \
  > server.log 2>&1 < /dev/null &
echo "[OK] 启动中 (PID $!)  模型: $MODEL"
echo "     日志: $DIR/server.log   就绪判断: curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9877/"
