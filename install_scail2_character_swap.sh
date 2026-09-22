#!/usr/bin/env bash
# Install models + MeetMap runtime support for the integrated SCAIL-2 character swap V2 workflow.
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
  for candidate in /workspace/runpod-slim/ComfyUI /workspace/ComfyUI /ComfyUI /opt/ComfyUI; do
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

ensure_meetmap_repo() {
  local destination="$1"
  if [[ -d "$destination/.git" ]]; then
    git -C "$destination" fetch --depth 1 origin main
    git -C "$destination" checkout -B main FETCH_HEAD
  else
    rm -rf "$destination"
    git clone --depth 1 "$MEETMAP_REPO_URL" "$destination"
  fi
}

main() {
  local comfyui_dir python_bin custom_nodes meetmap_dir models_dir
  comfyui_dir="$(find_comfyui)"
  python_bin="$(find_python "$comfyui_dir")"
  custom_nodes="$comfyui_dir/custom_nodes"
  meetmap_dir="$custom_nodes/ComfyUI-MeetMap-UGC"
  models_dir="$comfyui_dir/models"

  mkdir -p "$custom_nodes"
  ensure_meetmap_repo "$meetmap_dir"

  if ! "$python_bin" -c "import llama_cpp, huggingface_hub" >/dev/null 2>&1; then
    "$python_bin" -m pip install -r "$meetmap_dir/requirements.txt"
  fi
  "$python_bin" -m py_compile "$meetmap_dir/nodes.py" "$meetmap_dir/reference_nodes.py" "$meetmap_dir/__init__.py"

  "$python_bin" - "$comfyui_dir" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
required = {
    "comfy_extras/nodes_scail.py": ["class WanSCAILToVideo", "class SCAIL2ColoredMask"],
    "comfy_extras/nodes_sam3.py": ["class SAM3_VideoTrack"],
    "comfy_extras/nodes_audio.py": ["class TrimAudioDuration"],
    "comfy_extras/nodes_mask.py": ["class ImageCompositeMasked", "class FeatherMask", "class SolidMask"],
}
missing = []
for rel, symbols in required.items():
    p = root / rel
    if not p.is_file():
        missing.append(rel + " (file missing)")
        continue
    text = p.read_text(encoding="utf-8", errors="ignore")
    for symbol in symbols:
        if symbol not in text:
            missing.append(rel + ": " + symbol)
if missing:
    raise SystemExit(
        "ComfyUI is too old for the SCAIL-2 V2 workflow. Update ComfyUI first. Missing: "
        + "; ".join(missing)
    )
print("[MeetMap SCAIL] ComfyUI capability check passed.")
PY

  echo "[MeetMap SCAIL] Downloading current official Int8 Base + DPO model set..."
  "$python_bin" - "$models_dir" <<'PY'
import os
from pathlib import Path
import sys
from huggingface_hub import hf_hub_download

models = Path(sys.argv[1])
token = os.environ.get("HF_TOKEN") or None
specs = [
    ("Comfy-Org/SCAIL-2", "diffusion_models/wan2.1_14B_SCAIL_2_int8_convrot.safetensors",
     models / "diffusion_models/wan2.1_14B_SCAIL_2_int8_convrot.safetensors"),
    ("Comfy-Org/SCAIL-2", "loras/wan2.1_SCAIL_2_DPO_lora_bf16.safetensors",
     models / "loras/wan2.1_SCAIL_2_DPO_lora_bf16.safetensors"),
    ("Kijai/WanVideo_comfy", "Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
     models / "loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"),
    ("Kijai/WanVideo_comfy", "Wan2_1_VAE_bf16.safetensors",
     models / "vae/Wan2_1_VAE_bf16.safetensors"),
    ("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
     models / "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
    ("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/clip_vision/clip_vision_h.safetensors",
     models / "clip_vision/clip_vision_h.safetensors"),
    ("Comfy-Org/sam3.1", "checkpoints/sam3.1_multiplex_fp16.safetensors",
     models / "checkpoints/sam3.1_multiplex_fp16.safetensors"),
]

for repo, filename, target in specs:
    if target.is_file() and target.stat().st_size > 0:
        print(f"[MeetMap SCAIL] present: {target}")
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    hf_hub_download(
        repo_id=repo,
        filename=filename,
        local_dir=str(models),
        token=token,
    )
    if not target.is_file() or target.stat().st_size <= 0:
        raise SystemExit(f"Model download failed or landed at an unexpected path: {target}")
    print(f"[MeetMap SCAIL] downloaded: {target}")
PY

  mkdir -p "$comfyui_dir/user/default/workflows"
  cp "$meetmap_dir/workflows/meetmap_scail2_character_swap_v2.json" \
     "$comfyui_dir/user/default/workflows/meetmap_scail2_character_swap_v2.json"

  [[ -s "$comfyui_dir/user/default/workflows/meetmap_scail2_character_swap_v2.json" ]] || {
    echo "Workflow copy failed." >&2
    exit 1
  }

  echo "[MeetMap SCAIL] Installation complete."
  echo "[MeetMap SCAIL] Restart the Pod / ComfyUI process before loading the workflow."
  echo "[MeetMap SCAIL] Workflow: meetmap_scail2_character_swap_v2.json"
}

main "$@"
