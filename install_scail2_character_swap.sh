#!/usr/bin/env bash
# Install models + MeetMap runtime support for SCAIL-2 character swap V2/V3 (Google Drive automation).
set -euo pipefail
unset PIP_CONSTRAINT

readonly MEETMAP_REPO_URL="https://github.com/xxHalloxx/ComfyUI-MeetMap-UGC.git"
readonly SEEDVC_REPO_URL="https://github.com/billwuhao/ComfyUI_Seed-VC.git"
readonly SEEDVC_COMMIT="02c0cb8b05121dd9e391b4287c8e28e0eb4e79a4"

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

ensure_seedvc_repo() {
  local destination="$1"
  if [[ -d "$destination/.git" ]]; then
    git -C "$destination" fetch --depth 1 origin "$SEEDVC_COMMIT"
  else
    rm -rf "$destination"
    git clone "$SEEDVC_REPO_URL" "$destination"
  fi
  git -C "$destination" checkout --detach "$SEEDVC_COMMIT"
}

main() {
  local comfyui_dir python_bin custom_nodes meetmap_dir seedvc_dir models_dir input_dir
  comfyui_dir="$(find_comfyui)"
  python_bin="$(find_python "$comfyui_dir")"
  custom_nodes="$comfyui_dir/custom_nodes"
  meetmap_dir="$custom_nodes/ComfyUI-MeetMap-UGC"
  seedvc_dir="$custom_nodes/ComfyUI_Seed-VC"
  models_dir="$comfyui_dir/models"
  input_dir="$comfyui_dir/input"

  mkdir -p "$custom_nodes"
  ensure_meetmap_repo "$meetmap_dir"
  ensure_seedvc_repo "$seedvc_dir"

  if ! "$python_bin" -c "import llama_cpp, huggingface_hub, googleapiclient; from google.oauth2 import service_account" >/dev/null 2>&1; then
    "$python_bin" -m pip install -r "$meetmap_dir/requirements.txt"
  fi

  echo "[MeetMap SCAIL] Installing pinned Seed-VC runtime dependencies..."
  "$python_bin" -m pip install -r "$seedvc_dir/requirements.txt"
  "$python_bin" -m py_compile \
    "$meetmap_dir/nodes.py" \
    "$meetmap_dir/reference_nodes.py" \
    "$meetmap_dir/gdrive_nodes.py" \
    "$meetmap_dir/voice_nodes.py" \
    "$meetmap_dir/__init__.py" \
    "$seedvc_dir/seedvcnode.py"

  # Copy repo-managed creator identity references into the path used by V3.
  mkdir -p "$input_dir/meetmap_refs"
  if [[ -d "$meetmap_dir/refs/creators" ]]; then
    for creator_dir in "$meetmap_dir"/refs/creators/*; do
      [[ -d "$creator_dir" ]] || continue
      creator_name="$(basename "$creator_dir")"
      mkdir -p "$input_dir/meetmap_refs/$creator_name"
      cp -a "$creator_dir/." "$input_dir/meetmap_refs/$creator_name/"
    done
  fi

  "$python_bin" - "$comfyui_dir" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
required = {
    "comfy_extras/nodes_scail.py": ["class WanSCAILToVideo", "class SCAIL2ColoredMask"],
    "comfy_extras/nodes_sam3.py": ["class SAM3_VideoTrack"],
    "comfy_extras/nodes_audio.py": ["class TrimAudioDuration"],
    "comfy_extras/nodes_mask.py": ["class ImageCompositeMasked", "class FeatherMask", "class SolidMask"],
    "comfy_extras/nodes_flux.py": ["class Flux2Scheduler", "class EmptyFlux2LatentImage"],
    "comfy_extras/nodes_edit_model.py": ["class ReferenceLatent"],
    "comfy_extras/nodes_post_processing.py": ["class ImageScaleToTotalPixels"],
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
        "ComfyUI is too old for the SCAIL-2 V3 workflow. Update ComfyUI first. Missing: "
        + "; ".join(missing)
    )
print("[MeetMap SCAIL] ComfyUI capability check passed.")
PY

  echo "[MeetMap SCAIL] Downloading SCAIL-2 + FLUX.2 + Seed-VC model set..."
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
    ("black-forest-labs/FLUX.2-klein-4b-fp8", "flux-2-klein-4b-fp8.safetensors",
     models / "diffusion_models/flux-2-klein-4b-fp8.safetensors"),
    ("Comfy-Org/z_image_turbo", "split_files/text_encoders/qwen_3_4b.safetensors",
     models / "text_encoders/qwen_3_4b.safetensors"),
    ("Comfy-Org/flux2-dev", "split_files/vae/flux2-vae.safetensors",
     models / "vae/flux2-vae.safetensors"),

    # Seed-VC checkpoints.
    ("Plachta/Seed-VC", "DiT_seed_v2_uvit_whisper_small_wavenet_bigvgan_pruned.pth",
     models / "TTS/Seed-VC/DiT_seed_v2_uvit_whisper_small_wavenet_bigvgan_pruned.pth"),
    ("Plachta/Seed-VC", "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema.pth",
     models / "TTS/Seed-VC/DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema.pth"),
    ("funasr/campplus", "campplus_cn_common.bin",
     models / "TTS/Seed-VC/campplus_cn_common.bin"),
    ("lj1995/VoiceConversionWebUI", "rmvpe.pt",
     models / "TTS/Seed-VC/rmvpe.pt"),

    # Seed-VC BigVGAN vocoders.
    ("nvidia/bigvgan_v2_22khz_80band_256x", "config.json",
     models / "TTS/bigvgan_v2_22khz_80band_256x/config.json"),
    ("nvidia/bigvgan_v2_22khz_80band_256x", "bigvgan_generator.pt",
     models / "TTS/bigvgan_v2_22khz_80band_256x/bigvgan_generator.pt"),
    ("nvidia/bigvgan_v2_44khz_128band_512x", "config.json",
     models / "TTS/bigvgan_v2_44khz_128band_512x/config.json"),
    ("nvidia/bigvgan_v2_44khz_128band_512x", "bigvgan_generator.pt",
     models / "TTS/bigvgan_v2_44khz_128band_512x/bigvgan_generator.pt"),

    # Local Whisper encoder used by Seed-VC.
    ("openai/whisper-small", "model.safetensors",
     models / "TTS/whisper-small/model.safetensors"),
    ("openai/whisper-small", "config.json",
     models / "TTS/whisper-small/config.json"),
    ("openai/whisper-small", "preprocessor_config.json",
     models / "TTS/whisper-small/preprocessor_config.json"),
    ("openai/whisper-small", "generation_config.json",
     models / "TTS/whisper-small/generation_config.json"),
    ("openai/whisper-small", "tokenizer.json",
     models / "TTS/whisper-small/tokenizer.json"),
    ("openai/whisper-small", "tokenizer_config.json",
     models / "TTS/whisper-small/tokenizer_config.json"),
    ("openai/whisper-small", "special_tokens_map.json",
     models / "TTS/whisper-small/special_tokens_map.json"),
    ("openai/whisper-small", "added_tokens.json",
     models / "TTS/whisper-small/added_tokens.json"),
    ("openai/whisper-small", "merges.txt",
     models / "TTS/whisper-small/merges.txt"),
    ("openai/whisper-small", "normalizer.json",
     models / "TTS/whisper-small/normalizer.json"),
    ("openai/whisper-small", "vocab.json",
     models / "TTS/whisper-small/vocab.json"),
]

for repo, filename, target in specs:
    if target.is_file() and target.stat().st_size > 0:
        print(f"[MeetMap SCAIL] present: {target}")
        continue

    import shutil
    target.parent.mkdir(parents=True, exist_ok=True)
    downloaded = Path(
        hf_hub_download(
            repo_id=repo,
            filename=filename,
            local_dir=str(target.parent),
            token=token,
        )
    )
    if downloaded.resolve() != target.resolve():
        target.unlink(missing_ok=True)
        shutil.move(str(downloaded), str(target))

    if not target.is_file() or target.stat().st_size <= 0:
        raise SystemExit(f"Model download failed: {target}")
    print(f"[MeetMap SCAIL] downloaded: {target}")
PY

  mkdir -p "$comfyui_dir/user/default/workflows"
  cp "$meetmap_dir/workflows/meetmap_scail2_character_swap_v2.json" \
     "$comfyui_dir/user/default/workflows/meetmap_scail2_character_swap_v2.json"
  cp "$meetmap_dir/workflows/meetmap_scail2_character_swap_v3_drive.json" \
     "$comfyui_dir/user/default/workflows/meetmap_scail2_character_swap_v3_drive.json"

  [[ -s "$comfyui_dir/user/default/workflows/meetmap_scail2_character_swap_v3_drive.json" ]] || {
    echo "V3 workflow copy failed." >&2
    exit 1
  }

  voice_ref="$input_dir/meetmap_refs/creator_01/voice/reference.wav"
  if [[ -s "$voice_ref" ]]; then
    echo "[MeetMap SCAIL] Creator voice reference present: $voice_ref"
  else
    echo "[MeetMap SCAIL] WARNING: creator voice reference is missing: $voice_ref" >&2
    echo "[MeetMap SCAIL] Add a clean reference.wav before running V3 voice conversion." >&2
  fi

  echo "[MeetMap SCAIL] Installation complete."
  echo "[MeetMap SCAIL] V3 Drive automation requires:"
  echo "  GOOGLE_SERVICE_ACCOUNT_JSON=<service account JSON secret>"
  echo "  MEETMAP_MOTION_DRIVE_FOLDER_ID=<folder id of MeetMap TikTok Content/Queue>"
  echo "  optional: MEETMAP_MOTION_EXPECTED_FOLDER_NAME=Queue"
  echo "  optional: MEETMAP_MOTION_EXPECTED_PARENT_FOLDER_NAME=MeetMap TikTok Content"
  echo "  optional: MEETMAP_MOTION_PROCESSED_FOLDER_ID=<override destination folder id>"
  echo "  optional: MEETMAP_MOTION_PROCESSED_FOLDER_NAME=Already posted"
  echo "[MeetMap SCAIL] The runtime refuses source folders outside MeetMap TikTok Content/Queue."
  echo "[MeetMap SCAIL] Successful sources are moved to sibling folder 'Already posted'."
  echo "[MeetMap SCAIL] Seed-VC uses input/meetmap_refs/creator_01/voice/reference.wav as the fixed voice."
  echo "[MeetMap SCAIL] Share the Queue path with the service-account email as Editor."
  echo "[MeetMap SCAIL] Restart the Pod / ComfyUI process before loading the workflow."
  echo "[MeetMap SCAIL] Recommended workflow: meetmap_scail2_character_swap_v3_drive.json"
}

main "$@"
