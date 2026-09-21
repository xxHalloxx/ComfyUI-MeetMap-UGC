#!/usr/bin/env bash
# Run from any directory on a standard RunPod ComfyUI pod.
set -euo pipefail
unset PIP_CONSTRAINT

comfyui_dir="${COMFYUI_DIR:-}"
if [[ -z "$comfyui_dir" ]]; then
  for candidate in /workspace/ComfyUI /workspace/runpod-slim /workspace/runpod-slim/ComfyUI /ComfyUI /opt/ComfyUI; do
    if [[ -f "$candidate/main.py" ]]; then comfyui_dir="$candidate"; break; fi
  done
fi
[[ -n "$comfyui_dir" ]] || { echo "ComfyUI not found; set COMFYUI_DIR." >&2; exit 1; }
[[ -n "${HF_TOKEN:-}" ]] || { echo "HF_TOKEN fehlt; provisioning stops before downloads." >&2; exit 1; }

repo_dir="$comfyui_dir/custom_nodes/ComfyUI-MeetMap-UGC"
mkdir -p "$comfyui_dir/custom_nodes"
if [[ -d "$repo_dir/.git" ]]; then
  git -C "$repo_dir" fetch --depth 1 origin main
  git -C "$repo_dir" checkout --detach FETCH_HEAD
else
  git clone --depth 1 https://github.com/xxHalloxx/ComfyUI-MeetMap-UGC.git "$repo_dir"
fi
exec bash "$repo_dir/install_runpod.sh"
