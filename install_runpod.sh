#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
  echo "ComfyUI not found. Set COMFYUI_DIR to the ComfyUI installation directory." >&2
  exit 1
fi

if [[ -x "${COMFYUI_DIR}/venv/bin/python" ]]; then
  PYTHON_BIN="${COMFYUI_DIR}/venv/bin/python"
elif [[ -x "${COMFYUI_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${COMFYUI_DIR}/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "[MeetMap UGC] ComfyUI: ${COMFYUI_DIR}"
echo "[MeetMap UGC] Python: ${PYTHON_BIN}"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required but is not installed." >&2
  exit 1
fi

if ! command -v cmake >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1 && [[ "$(id -u)" -eq 0 ]]; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends build-essential cmake
  rm -rf /var/lib/apt/lists/*
fi

mkdir -p "${COMFYUI_DIR}/custom_nodes" "${COMFYUI_DIR}/models/LLM"

CMAKE_ARGS="-DGGML_CUDA=OFF" FORCE_CMAKE=1   "${PYTHON_BIN}" -m pip install --no-cache-dir -r "${SCRIPT_DIR}/requirements.txt"

install_repo() {
  local repo_url="$1"
  local commit="$2"
  local destination="$3"

  if [[ ! -d "${destination}/.git" ]]; then
    git clone --filter=blob:none "${repo_url}" "${destination}"
  fi

  git -C "${destination}" fetch --depth 1 origin "${commit}"
  git -C "${destination}" checkout --detach "${commit}"
}

install_repo   "https://github.com/ltdrdata/ComfyUI-Impact-Pack.git"   "429d0159ad429e64d2b3916e6e7be9c22d025c3c"   "${COMFYUI_DIR}/custom_nodes/ComfyUI-Impact-Pack"

install_repo   "https://github.com/ltdrdata/ComfyUI-Impact-Subpack.git"   "50c7b71a6a224734cc9b21963c6d1926816a97f1"   "${COMFYUI_DIR}/custom_nodes/ComfyUI-Impact-Subpack"

"${PYTHON_BIN}" -m pip install --no-cache-dir -r "${COMFYUI_DIR}/custom_nodes/ComfyUI-Impact-Pack/requirements.txt"
"${PYTHON_BIN}" -m pip install --no-cache-dir -r "${COMFYUI_DIR}/custom_nodes/ComfyUI-Impact-Subpack/requirements.txt"

echo
echo "[MeetMap UGC] Installation complete."
echo "[MeetMap UGC] Restart ComfyUI if it is already running."
echo "[MeetMap UGC] The default Qwen GGUF downloads automatically on the first Queue run if missing."
