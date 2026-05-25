#!/usr/bin/env bash
set -Eeuo pipefail

COMFY_SESSION="${COMFY_TMUX_SESSION:-comfyui}"
WORKER_SESSION="${WORKER_TMUX_SESSION:-infinitetalk-worker}"

COMFYUI_DIR="${COMFYUI_DIR:-/root/ComfyUI}"
COMFYUI_PORT="${COMFYUI_PORT:-8080}"
COMFYUI_EXTRA_ARGS="${COMFYUI_EXTRA_ARGS:-}"

PROJECT_DIR="${PROJECT_DIR:-/root/infinitetalk}"
ENV_FILE="${WORKER_ENV_FILE:-}"
COMFYUI_STARTUP_WAIT_SECONDS="${COMFYUI_STARTUP_WAIT_SECONDS:-3}"
DOWNLOAD_MODEL_SCRIPT="${DOWNLOAD_MODEL_SCRIPT:-/root/shells/download_model.sh}"
DOWNLOAD_MODELS=("umt5" "wan2.1-i2v-480p")

shell_quote() {
  local value=${1//\'/\'\\\'\'}
  printf "'%s'" "$value"
}

stop_session_if_exists() {
  local session_name="$1"

  if tmux has-session -t "$session_name" 2>/dev/null; then
    echo "Stopping existing tmux session: $session_name"
    tmux kill-session -t "$session_name"
  fi
}

download_models() {
  local model_name

  if [[ ! -f "$DOWNLOAD_MODEL_SCRIPT" ]]; then
    echo "Model download script not found: $DOWNLOAD_MODEL_SCRIPT"
    exit 1
  fi

  for model_name in "${DOWNLOAD_MODELS[@]}"; do
    echo "Downloading model: $model_name"
    bash "$DOWNLOAD_MODEL_SCRIPT" "$model_name"
  done
}

start_comfyui() {
  local python_bin
  local run_script

  if [[ -n "${COMFYUI_PYTHON:-}" ]]; then
    python_bin="$COMFYUI_PYTHON"
  elif [[ -x "$COMFYUI_DIR/venv/bin/python" ]]; then
    python_bin="$COMFYUI_DIR/venv/bin/python"
  elif [[ -x "$COMFYUI_DIR/.venv/bin/python" ]]; then
    python_bin="$COMFYUI_DIR/.venv/bin/python"
  else
    python_bin="python"
  fi

  run_script="exec $(shell_quote "$python_bin") main.py --use-sage-attention --port $(shell_quote "$COMFYUI_PORT")"
  if [[ -n "$COMFYUI_EXTRA_ARGS" ]]; then
    run_script="$run_script $COMFYUI_EXTRA_ARGS"
  fi

  tmux new-session -d -s "$COMFY_SESSION" -c "$COMFYUI_DIR" "$run_script"
  echo "Started ComfyUI in tmux session: $COMFY_SESSION"
}

start_worker() {
  local python_bin
  local workflow_path
  local run_script

  if [[ -z "$ENV_FILE" && -f "$PROJECT_DIR/.env.worker" ]]; then
    ENV_FILE="$PROJECT_DIR/.env.worker"
  fi

  if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_FILE" ]]; then
      echo "Worker env file not found: $ENV_FILE"
      exit 1
    fi
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi

  WORKER_TOKEN="${WORKER_TOKEN:-change-me}"
  WORKER_ID="${WORKER_ID:-gpu-worker-01}"
  ROUTER_WS_URL="${ROUTER_WS_URL:-https://dongdongkc.shierkeji.com:6205/}"
  COMFYUI_BASE_URL="${COMFYUI_BASE_URL:-http://127.0.0.1:8080}"
  WORKFLOW_FILE="${WORKFLOW_FILE:-./workflows/kijai-wanvideo_I2V_InfiniteTalk_example_01.json}"
  IMAGE_NODE_ID="${IMAGE_NODE_ID:-284}"
  AUDIO_NODE_ID="${AUDIO_NODE_ID:-125}"
  VIDEO_NODE_ID="${VIDEO_NODE_ID:-131}"
  WIDTH_NODE_ID="${WIDTH_NODE_ID:-245}"
  HEIGHT_NODE_ID="${HEIGHT_NODE_ID:-246}"
  POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-2}"
  POLL_TIMEOUT_SECONDS="${POLL_TIMEOUT_SECONDS:-600}"
  WORKER_TMP_DIR="${WORKER_TMP_DIR:-./worker_data/inflight}"
  WORKER_RECONNECT_SECONDS="${WORKER_RECONNECT_SECONDS:-5}"
  WORKER_HEARTBEAT_SECONDS="${WORKER_HEARTBEAT_SECONDS:-25}"

  if [[ -n "${WORKER_PYTHON:-}" ]]; then
    python_bin="$WORKER_PYTHON"
  elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    python_bin="$PROJECT_DIR/.venv/bin/python"
  elif [[ -x "$PROJECT_DIR/venv/bin/python" ]]; then
    python_bin="$PROJECT_DIR/venv/bin/python"
  else
    python_bin="python"
  fi

  workflow_path="$WORKFLOW_FILE"
  if [[ "$workflow_path" != /* ]]; then
    workflow_path="$PROJECT_DIR/$workflow_path"
  fi

  if [[ ! -f "$workflow_path" ]]; then
    echo "Workflow file not found: $workflow_path"
    exit 1
  fi

  run_script="export WORKER_TOKEN=$(shell_quote "$WORKER_TOKEN"); "
  run_script+="export WORKER_ID=$(shell_quote "$WORKER_ID"); "
  run_script+="export ROUTER_WS_URL=$(shell_quote "$ROUTER_WS_URL"); "
  run_script+="export COMFYUI_BASE_URL=$(shell_quote "$COMFYUI_BASE_URL"); "
  run_script+="export WORKFLOW_FILE=$(shell_quote "$WORKFLOW_FILE"); "
  run_script+="export IMAGE_NODE_ID=$(shell_quote "$IMAGE_NODE_ID"); "
  run_script+="export AUDIO_NODE_ID=$(shell_quote "$AUDIO_NODE_ID"); "
  run_script+="export VIDEO_NODE_ID=$(shell_quote "$VIDEO_NODE_ID"); "
  run_script+="export WIDTH_NODE_ID=$(shell_quote "$WIDTH_NODE_ID"); "
  run_script+="export HEIGHT_NODE_ID=$(shell_quote "$HEIGHT_NODE_ID"); "
  run_script+="export POLL_INTERVAL_SECONDS=$(shell_quote "$POLL_INTERVAL_SECONDS"); "
  run_script+="export POLL_TIMEOUT_SECONDS=$(shell_quote "$POLL_TIMEOUT_SECONDS"); "
  run_script+="export WORKER_TMP_DIR=$(shell_quote "$WORKER_TMP_DIR"); "
  run_script+="export WORKER_RECONNECT_SECONDS=$(shell_quote "$WORKER_RECONNECT_SECONDS"); "
  run_script+="export WORKER_HEARTBEAT_SECONDS=$(shell_quote "$WORKER_HEARTBEAT_SECONDS"); "
  run_script+="exec $(shell_quote "$python_bin") worker_app.py"

  tmux new-session -d -s "$WORKER_SESSION" -c "$PROJECT_DIR" "$run_script"
  echo "Started InfiniteTalk Worker in tmux session: $WORKER_SESSION"
}

download_models

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is not installed. Please install tmux first."
  exit 1
fi

if [[ ! -d "$COMFYUI_DIR" ]]; then
  echo "ComfyUI directory not found: $COMFYUI_DIR"
  exit 1
fi

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "InfiniteTalk project directory not found: $PROJECT_DIR"
  exit 1
fi

stop_session_if_exists "$WORKER_SESSION"
stop_session_if_exists "$COMFY_SESSION"

start_comfyui

if [[ "$COMFYUI_STARTUP_WAIT_SECONDS" != "0" ]]; then
  echo "Waiting ${COMFYUI_STARTUP_WAIT_SECONDS}s before starting Worker..."
  sleep "$COMFYUI_STARTUP_WAIT_SECONDS"
fi

start_worker

echo ""
echo "tmux sessions restarted:"
echo "  ComfyUI: $COMFY_SESSION"
echo "  Worker:  $WORKER_SESSION"
echo ""
echo "Attach: tmux attach -t $COMFY_SESSION"
echo "Attach: tmux attach -t $WORKER_SESSION"
echo "Logs:   tmux capture-pane -pt $COMFY_SESSION -S -200"
echo "Logs:   tmux capture-pane -pt $WORKER_SESSION -S -200"