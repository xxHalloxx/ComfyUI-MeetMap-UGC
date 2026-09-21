#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "${SCRIPT_DIR}/install_runpod.sh"

if [[ -n "${COMFYUI_DIR:-}" && -f "${COMFYUI_DIR}/main.py" ]]; then
  :
else
  inferred="$(cd "${SCRIPT_DIR}/../.." 2>/dev/null && pwd || true)"
  COMFYUI_DIR=""
  for candidate in "${inferred}" /workspace/ComfyUI /ComfyUI /opt/ComfyUI; do
    if [[ -n "${candidate}" && -f "${candidate}/main.py" ]]; then
      COMFYUI_DIR="${candidate}"
      break
    fi
  done
fi

if [[ -z "${COMFYUI_DIR:-}" || ! -f "${COMFYUI_DIR}/main.py" ]]; then
  echo "ComfyUI not found after installation." >&2
  exit 1
fi

if [[ -x "${COMFYUI_DIR}/venv/bin/python" ]]; then
  PYTHON_BIN="${COMFYUI_DIR}/venv/bin/python"
elif [[ -x "${COMFYUI_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${COMFYUI_DIR}/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

cd "${COMFYUI_DIR}"
exec "${PYTHON_BIN}" main.py --listen 0.0.0.0 --port "${COMFYUI_PORT:-8188}"
