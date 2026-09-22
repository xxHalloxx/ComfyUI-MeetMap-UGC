#!/usr/bin/env bash
# Minimal provisioner for MeetMap MiniMax H3 V2 on a standard RunPod ComfyUI pod.
# Goal: install only what meetmap_minimax_h3_i2v_app_v2.json needs.
set -euo pipefail
unset PIP_CONSTRAINT

readonly MEETMAP_REPO_URL="https://github.com/xxHalloxx/ComfyUI-MeetMap-UGC.git"

find_comfyui() {
  local candidate
  if [[ -n "${COMFYUI_DIR:-}" ]]; then
    candidate="$COMFYUI_DIR"
    [[ -f "$candidate/main.py" ]] || { echo "COMFYUI_DIR does not contain main.py: $candidate" >&2; return 1; }
    printf '%s\n' "$candidate"
    return
  fi
  for candidate in /workspace/ComfyUI /workspace/runpod-slim /workspace/runpod-slim/ComfyUI /ComfyUI /opt/ComfyUI; do
    if [[ -f "$candidate/main.py" ]]; then printf '%s\n' "$candidate"; return; fi
  done
  echo "ComfyUI not found. Set COMFYUI_DIR to the directory containing main.py." >&2
  return 1
}

find_python() {
  local root="$1" candidate
  for candidate in "$root/venv/bin/python" "$root/.venv/bin/python"; do
    [[ -x "$candidate" ]] && { printf '%s\n' "$candidate"; return; }
  done
  command -v python3 || command -v python || { echo "No usable Python interpreter found." >&2; return 1; }
}

ensure_meetmap_repo() {
  local destination="$1"
  if [[ -d "$destination/.git" ]]; then
    git -C "$destination" fetch --depth 1 origin main
    git -C "$destination" checkout -B main FETCH_HEAD
  elif [[ ! -e "$destination" ]]; then
    git clone --depth 1 "$MEETMAP_REPO_URL" "$destination"
  else
    echo "Existing MeetMap custom-node path is not a git checkout: $destination" >&2
    return 1
  fi
}

core_has_required_nodes() {
  local root="$1"
  [[ -f "$root/comfy_extras/nodes_qwen.py" ]] || return 1
  [[ -f "$root/comfy_extras/nodes_minimax_h3.py" ]] || return 1
  grep -q "class TextEncodeQwenImage21" "$root/comfy_extras/nodes_qwen.py" || return 1
  grep -q "class QwenImage21Cache" "$root/comfy_extras/nodes_qwen.py" || return 1
  grep -q "class MiniMaxH3ImageToVideo" "$root/comfy_extras/nodes_minimax_h3.py" || return 1
  return 0
}

try_update_comfyui_core() {
  local root="$1" python_bin="$2"
  if core_has_required_nodes "$root"; then
    echo "[MeetMap H3 V2] ComfyUI core already supports Qwen Image 2.1 + MiniMax H3."
    return 0
  fi

  echo "[MeetMap H3 V2] ComfyUI core is too old. Attempting a safe update..."

  [[ -d "$root/.git" ]] || {
    echo "ComfyUI is not a git checkout, so the installer cannot safely update it automatically." >&2
    echo "Use a current RunPod ComfyUI preset, then rerun this installer." >&2
    return 1
  }

  if [[ -n "$(git -C "$root" status --porcelain --untracked-files=no)" ]]; then
    echo "ComfyUI has tracked local modifications. Refusing to overwrite them." >&2
    echo "Start a fresh current ComfyUI preset or update ComfyUI manually, then rerun." >&2
    return 1
  fi

  local remote_ref=""
  if git -C "$root" fetch --depth 1 origin master; then
    remote_ref="FETCH_HEAD"
  elif git -C "$root" fetch --depth 1 origin main; then
    remote_ref="FETCH_HEAD"
  else
    echo "Could not fetch a current ComfyUI core from the existing origin remote." >&2
    return 1
  fi

  git -C "$root" checkout --detach "$remote_ref"
  "$python_bin" -m pip install -r "$root/requirements.txt"

  core_has_required_nodes "$root" || {
    echo "ComfyUI update completed, but Qwen Image 2.1 / MiniMax H3 support is still missing." >&2
    return 1
  }
  echo "[MeetMap H3 V2] ComfyUI core updated successfully."
}

main() {
  local comfyui_dir python_bin custom_nodes meetmap_dir models_dir input_dir
  comfyui_dir="$(find_comfyui)"
  python_bin="$(find_python "$comfyui_dir")"
  custom_nodes="$comfyui_dir/custom_nodes"
  meetmap_dir="$custom_nodes/ComfyUI-MeetMap-UGC"
  models_dir="$comfyui_dir/models"
  input_dir="$comfyui_dir/input/meetmap_refs"

  mkdir -p "$custom_nodes" "$models_dir" "$input_dir"

  try_update_comfyui_core "$comfyui_dir" "$python_bin"

  ensure_meetmap_repo "$meetmap_dir"
  "$python_bin" -m pip install -r "$meetmap_dir/requirements.txt"
  "$python_bin" -m py_compile     "$meetmap_dir/nodes.py"     "$meetmap_dir/reference_nodes.py"     "$meetmap_dir/github_ref_nodes.py"     "$meetmap_dir/__init__.py"

  # Seed a local reference cache. The workflow itself still checks GitHub on each run.
  if [[ -d "$meetmap_dir/refs/creators" ]]; then
    local creator_dir creator_name
    for creator_dir in "$meetmap_dir"/refs/creators/*; do
      [[ -d "$creator_dir" ]] || continue
      creator_name="$(basename "$creator_dir")"
      mkdir -p "$input_dir/$creator_name"
      cp -a "$creator_dir/." "$input_dir/$creator_name/"
    done
  fi

  "$python_bin" - "$models_dir" <<'PY'
from pathlib import Path
import shutil
import sys
from huggingface_hub import hf_hub_download

models = Path(sys.argv[1])

specs = [
    # Local content/script LLM
    (
        "bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF",
        "Qwen_Qwen3-4B-Instruct-2507-Q5_K_M.gguf",
        models / "LLM/Qwen_Qwen3-4B-Instruct-2507-Q5_K_M.gguf",
    ),

    # Qwen Image 2.1 multi-reference start-image generation
    (
        "Comfy-Org/Qwen-Image-2.1",
        "diffusion_models/qwen_image_2.1_int8_convrot.safetensors",
        models / "diffusion_models/qwen_image_2.1_int8_convrot.safetensors",
    ),
    (
        "Comfy-Org/Qwen-Image-2.1",
        "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
        models / "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
    ),
    (
        "Comfy-Org/Qwen-Image-2.1",
        "vae/qwen_image_2.1_vae_bf16.safetensors",
        models / "vae/qwen_image_2.1_vae_bf16.safetensors",
    ),

    # MiniMax H3 image-to-video + native audio
    (
        "Comfy-Org/MiniMax-H3",
        "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        models / "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    ),
    (
        "Comfy-Org/MiniMax-H3",
        "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        models / "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    ),
    (
        "Comfy-Org/MiniMax-H3",
        "vae/minimax_h3_video_vae_fp16.safetensors",
        models / "vae/minimax_h3_video_vae_fp16.safetensors",
    ),
    (
        "Comfy-Org/MiniMax-H3",
        "vae/minimax_h3_audio_vae_fp32.safetensors",
        models / "vae/minimax_h3_audio_vae_fp32.safetensors",
    ),
    (
        "lightx2v/Minimax-h3-Turbo",
        "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
        models / "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
    ),
]

for repo_id, filename, target in specs:
    if target.is_file() and target.stat().st_size > 0:
        print(f"[MeetMap H3 V2] present: {target}")
        continue

    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"[MeetMap H3 V2] downloading: {repo_id}/{filename}")
    downloaded = Path(
        hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            token=None,
        )
    )

    if downloaded.resolve() != target.resolve():
        shutil.copyfile(downloaded, target)

    if not target.is_file() or target.stat().st_size <= 0:
        raise SystemExit(f"Model download failed: {target}")

    print(f"[MeetMap H3 V2] ready: {target}")
PY

  local required_file
  for required_file in     "$models_dir/LLM/Qwen_Qwen3-4B-Instruct-2507-Q5_K_M.gguf"     "$models_dir/diffusion_models/qwen_image_2.1_int8_convrot.safetensors"     "$models_dir/text_encoders/qwen3vl_8b_int8_convrot.safetensors"     "$models_dir/vae/qwen_image_2.1_vae_bf16.safetensors"     "$models_dir/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors"     "$models_dir/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"     "$models_dir/vae/minimax_h3_video_vae_fp16.safetensors"     "$models_dir/vae/minimax_h3_audio_vae_fp32.safetensors"     "$models_dir/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"; do
    [[ -s "$required_file" ]] || {
      echo "[MeetMap H3 V2] ERROR: required file missing: $required_file" >&2
      exit 1
    }
  done

  local creator_ref_count
  creator_ref_count="$(find "$input_dir/creator_01" -maxdepth 1 -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.webp' \) -size +0c 2>/dev/null | wc -l | tr -d ' ')"
  [[ "${creator_ref_count:-0}" -ge 4 ]] || {
    echo "[MeetMap H3 V2] ERROR: expected at least 4 creator_01 reference images, found ${creator_ref_count:-0}." >&2
    exit 1
  }

  core_has_required_nodes "$comfyui_dir" || {
    echo "[MeetMap H3 V2] ERROR: final ComfyUI capability check failed." >&2
    exit 1
  }

  echo
  echo "=============================================================="
  echo "[MeetMap H3 V2] READY"
  echo "[MeetMap H3 V2] Custom nodes: installed"
  echo "[MeetMap H3 V2] Creator refs: $creator_ref_count images"
  echo "[MeetMap H3 V2] Qwen Image 2.1 models: installed"
  echo "[MeetMap H3 V2] MiniMax H3 models: installed"
  echo "[MeetMap H3 V2] Local script LLM: installed"
  echo "=============================================================="
  echo
  echo "NEXT:"
  echo "1. Restart the RunPod Pod / ComfyUI process."
  echo "2. Open meetmap_minimax_h3_i2v_app_v2.json in ComfyUI."
  echo "3. Press Run."
}

main "$@"
