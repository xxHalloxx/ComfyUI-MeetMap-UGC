#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/xxHalloxx/ComfyUI-MeetMap-UGC.git"
REPO_NAME="ComfyUI-MeetMap-UGC"

find_comfyui() {
  if [[ -n "${COMFYUI_DIR:-}" && -f "${COMFYUI_DIR}/main.py" ]]; then
    printf '%s\n' "${COMFYUI_DIR}"
    return 0
  fi

  local candidate
  for candidate in     /workspace/ComfyUI     /workspace/comfyui     /workspace/runpod-slim/ComfyUI     /workspace/runpod-slim/comfyui     /workspace/runpod-slim     /ComfyUI     /opt/ComfyUI     /opt/comfyui; do
    if [[ -f "${candidate}/main.py" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  local found
  found="$(find /workspace /opt -maxdepth 6 -type f -name main.py 2>/dev/null |     grep -Ei '/(ComfyUI|comfyui)(/|$)' | head -n 1 || true)"
  if [[ -n "${found}" ]]; then
    dirname "${found}"
    return 0
  fi

  return 1
}

COMFYUI_DIR="$(find_comfyui || true)"

if [[ -z "${COMFYUI_DIR}" ]]; then
  echo "[MeetMap UGC] Could not locate ComfyUI automatically." >&2
  echo "[MeetMap UGC] Run this diagnostic and send the output:" >&2
  echo "find /workspace /opt -maxdepth 6 -type f -name main.py 2>/dev/null | head -50" >&2
  exit 1
fi

echo "[MeetMap UGC] Found ComfyUI at: ${COMFYUI_DIR}"

CUSTOM_NODES_DIR="${COMFYUI_DIR}/custom_nodes"
DEST="${CUSTOM_NODES_DIR}/${REPO_NAME}"
mkdir -p "${CUSTOM_NODES_DIR}"

if [[ -d "${DEST}/.git" ]]; then
  echo "[MeetMap UGC] Updating existing repository..."
  git -C "${DEST}" pull --ff-only
else
  echo "[MeetMap UGC] Cloning custom nodes..."
  git clone "${REPO_URL}" "${DEST}"
fi

export COMFYUI_DIR
bash "${DEST}/install_runpod.sh"
