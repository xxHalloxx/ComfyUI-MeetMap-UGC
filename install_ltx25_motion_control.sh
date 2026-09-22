#!/usr/bin/env bash
# Install dependencies and official model set for MeetMap LTX 2.5 pose motion control.
set -euo pipefail
unset PIP_CONSTRAINT

readonly LTX_NODES_URL="https://github.com/Lightricks/ComfyUI-LTXVideo.git"
readonly LTX_NODES_REF="dfb2786749af36f200ea023388dc729a3e106b42"
readonly AUX_URL="https://github.com/Fannovel16/comfyui_controlnet_aux.git"
readonly AUX_REF="59b1fc411ede8623b2997855b8018f0b3b6cf49f"

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
  echo "ComfyUI not found. Set COMFYUI_DIR." >&2
  return 1
}

find_python() {
  local root="$1" candidate
  for candidate in "$root/venv/bin/python" "$root/.venv/bin/python"; do
    [[ -x "$candidate" ]] && { printf '%s\n' "$candidate"; return; }
  done
  command -v python3 || command -v python
}

ensure_repo() {
  local url="$1" destination="$2" ref="$3"
  if [[ -d "$destination/.git" ]]; then
    git -C "$destination" fetch --depth 1 origin "$ref"
    git -C "$destination" checkout --detach FETCH_HEAD
  else
    rm -rf "$destination"
    git clone --filter=blob:none "$url" "$destination"
    git -C "$destination" fetch --depth 1 origin "$ref"
    git -C "$destination" checkout --detach FETCH_HEAD
  fi
}

main() {
  [[ -n "${HF_TOKEN:-}" ]] || { echo "HF_TOKEN fehlt." >&2; exit 1; }

  local comfyui_dir python_bin custom_nodes meetmap_dir models_dir
  comfyui_dir="$(find_comfyui)"
  python_bin="$(find_python "$comfyui_dir")"
  custom_nodes="$comfyui_dir/custom_nodes"
  meetmap_dir="$custom_nodes/ComfyUI-MeetMap-UGC"
  models_dir="$comfyui_dir/models"

  [[ -d "$meetmap_dir" ]] || {
    echo "MeetMap runtime repo missing. Run bootstrap_runpod.sh first." >&2
    exit 1
  }

  mkdir -p "$custom_nodes" "$models_dir/loras"

  echo "[MeetMap Motion] Installing Lightricks LTX nodes..."
  ensure_repo "$LTX_NODES_URL" "$custom_nodes/ComfyUI-LTXVideo" "$LTX_NODES_REF"

  echo "[MeetMap Motion] Installing DWPose / controlnet auxiliary nodes..."
  ensure_repo "$AUX_URL" "$custom_nodes/comfyui_controlnet_aux" "$AUX_REF"

  [[ ! -f "$custom_nodes/ComfyUI-LTXVideo/requirements.txt" ]] || \
    "$python_bin" -m pip install -r "$custom_nodes/ComfyUI-LTXVideo/requirements.txt"

  [[ ! -f "$custom_nodes/comfyui_controlnet_aux/requirements.txt" ]] || \
    "$python_bin" -m pip install -r "$custom_nodes/comfyui_controlnet_aux/requirements.txt"

  echo "[MeetMap Motion] Downloading official LTX 2.5 Union-Control model set..."
  "$python_bin" - "$models_dir" <<'PY'
from pathlib import Path
import shutil
import sys
from huggingface_hub import hf_hub_download

models = Path(sys.argv[1])
specs = [
    ("Lightricks/LTX-2.5", "diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors",
     models / "diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors"),
    ("Lightricks/LTX-2.5", "text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors",
     models / "text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors"),
    ("Comfy-Org/gemma-4", "text_encoders/gemma4_e2b_it_bf16.safetensors",
     models / "text_encoders/gemma4_e2b_it_bf16.safetensors"),
    ("Lightricks/LTX-2.5", "vae/ltx-2.5-video-vae-bf16.safetensors",
     models / "vae/ltx-2.5-video-vae-bf16.safetensors"),
    ("Lightricks/LTX-2.5", "vae/ltx-2.5-audio-vae-bf16.safetensors",
     models / "vae/ltx-2.5-audio-vae-bf16.safetensors"),
    ("Lightricks/LTX-2.5", "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
     models / "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"),
    ("Lightricks/LTX-2.3-22b-IC-LoRA-Union-Control",
     "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors",
     models / "loras/ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors"),
]

for repo, filename, target in specs:
    if target.is_file() and target.stat().st_size > 0:
        print(f"[MeetMap Motion] present: {target}")
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    downloaded = Path(hf_hub_download(
        repo_id=repo,
        filename=filename,
        local_dir=str(target.parent),
        token=True,
    ))
    if downloaded.resolve() != target.resolve():
        shutil.copyfile(downloaded, target)
        try:
            downloaded.unlink()
        except OSError:
            pass
    if not target.is_file() or target.stat().st_size <= 0:
        raise SystemExit(f"Model download failed: {target}")
    print(f"[MeetMap Motion] downloaded: {target}")
PY

  mkdir -p "$comfyui_dir/user/default/workflows"
  cp "$meetmap_dir/workflows/meetmap_ltx25_motion_control.json" \
     "$comfyui_dir/user/default/workflows/meetmap_ltx25_motion_control.json"
  cp "$meetmap_dir/workflows/meetmap_ltx25_motion_control_v2.json" \
     "$comfyui_dir/user/default/workflows/meetmap_ltx25_motion_control_v2.json"

  [[ -s "$comfyui_dir/user/default/workflows/meetmap_ltx25_motion_control_v2.json" ]] || {
    echo "Motion Control V2 workflow copy failed." >&2
    exit 1
  }

  echo "[MeetMap Motion] Installation complete."
  echo "[MeetMap Motion] Restart the Pod / ComfyUI process before loading the workflow."
  echo "[MeetMap Motion] Recommended workflow: meetmap_ltx25_motion_control_v2.json"
}

main "$@"
