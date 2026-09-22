#!/usr/bin/env bash
# Install models + MeetMap runtime support for SCAIL-2 character and Seed-VC voice swap.
set -euo pipefail
unset PIP_CONSTRAINT

readonly MEETMAP_REPO_URL="https://github.com/xxHalloxx/ComfyUI-MeetMap-UGC.git"
readonly SEEDVC_REPO_URL="https://github.com/billwuhao/ComfyUI_Seed-VC.git"
readonly SEEDVC_COMMIT="02c0cb8b05121dd9e391b4287c8e28e0eb4e79a4"
readonly CREATOR_VOICE_DRIVE_FILE_ID="1BjZUeye3fVAkA1DtvdlYdsNQzMY_gANt"
readonly CREATOR_VOICE_SHA256="e0c502c490c74bbda5226fae8fb95206bb6facf1eb2d1d2ff025b19f9ae62fb7"
readonly SCAIL2_RELIGHT_SHA256="80d338a7969c1b286c8f5c4996b37eb198d0864837fecb6c87c106ca74571a2b"

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
  local comfyui_dir python_bin custom_nodes meetmap_dir seedvc_dir models_dir input_dir voice_ref
  comfyui_dir="$(find_comfyui)"
  python_bin="$(find_python "$comfyui_dir")"
  custom_nodes="$comfyui_dir/custom_nodes"
  meetmap_dir="$custom_nodes/ComfyUI-MeetMap-UGC"
  seedvc_dir="$custom_nodes/ComfyUI_Seed-VC"
  models_dir="$comfyui_dir/models"
  input_dir="$comfyui_dir/input"
  voice_ref="$input_dir/meetmap_refs/creator_01/voice/reference.flac"

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
    "$meetmap_dir/scail_runtime_nodes.py" \
    "$meetmap_dir/tools/convert_scail2_lora.py" \
    "$meetmap_dir/__init__.py" \
    "$seedvc_dir/seedvcnode.py"

  # Copy repo-managed visual creator references into ComfyUI/input.
  mkdir -p "$input_dir/meetmap_refs"
  if [[ -d "$meetmap_dir/refs/creators" ]]; then
    for creator_dir in "$meetmap_dir"/refs/creators/*; do
      [[ -d "$creator_dir" ]] || continue
      creator_name="$(basename "$creator_dir")"
      mkdir -p "$input_dir/meetmap_refs/$creator_name"
      cp -a "$creator_dir/." "$input_dir/meetmap_refs/$creator_name/"
    done
  fi
  mkdir -p "$(dirname "$voice_ref")"

  "$python_bin" - "$comfyui_dir" "$seedvc_dir" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
seedvc = Path(sys.argv[2])
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

seedvc_node = seedvc / "seedvcnode.py"
if not seedvc_node.is_file():
    missing.append(str(seedvc_node) + " (file missing)")
elif "class SeedVCRun" not in seedvc_node.read_text(encoding="utf-8", errors="ignore"):
    missing.append(str(seedvc_node) + ": class SeedVCRun")

if missing:
    raise SystemExit(
        "ComfyUI/runtime is missing required SCAIL-2 or Seed-VC support: "
        + "; ".join(missing)
    )
print("[MeetMap SCAIL] ComfyUI + Seed-VC capability check passed.")
PY

  echo "[MeetMap SCAIL] Downloading SCAIL-2 + FLUX.2 + Seed-VC model set..."
  "$python_bin" - "$models_dir" <<'PY'
import os
from pathlib import Path
import shutil
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

    # Official SCAIL-2 relighting LoRA is distributed in SAT .pt format.
    # It is SHA-256 verified and converted below with the pinned official converter.
    ("zai-org/SCAIL-2", "model/relighting-lora.pt",
     models / "loras/.meetmap_scail2_relighting/relighting-lora.pt"),

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

  # Convert the official SAT-format relighting LoRA to the safetensors format
  # expected by ComfyUI. Verify the published Hugging Face SHA-256 before
  # torch.load touches the pickle-based source checkpoint.
  relight_sat="$models_dir/loras/.meetmap_scail2_relighting/relighting-lora.pt"
  relight_out="$models_dir/loras/scail2_relighting_lora_bf16.safetensors"
  if [[ ! -s "$relight_out" ]]; then
    [[ -s "$relight_sat" ]] || { echo "Missing SCAIL-2 relighting source checkpoint." >&2; exit 1; }
    actual_relight_sha="$(sha256sum "$relight_sat" | awk '{print $1}')"
    if [[ "$actual_relight_sha" != "$SCAIL2_RELIGHT_SHA256" ]]; then
      echo "SCAIL-2 relighting checkpoint SHA-256 mismatch. Refusing conversion." >&2
      exit 1
    fi
    echo "[MeetMap SCAIL] Converting official SCAIL-2 relighting LoRA to ComfyUI safetensors..."
    "$python_bin" "$meetmap_dir/tools/convert_scail2_lora.py" \
      --input "$relight_sat" \
      --output "$relight_out" \
      --dtype bfloat16 \
      --print-sample 3
  fi
  [[ -s "$relight_out" ]] || { echo "Relighting LoRA conversion failed." >&2; exit 1; }
  "$python_bin" - "$relight_out" <<'PY'
from pathlib import Path
import sys
from safetensors.torch import load_file

path = Path(sys.argv[1])
state = load_file(str(path), device="cpu")
if not state:
    raise SystemExit(f"Converted relighting LoRA contains no tensors: {path}")
required_suffixes = (".lora_down.weight", ".lora_up.weight")
if not any(key.endswith(required_suffixes) for key in state):
    raise SystemExit(f"Converted relighting LoRA has no expected LoRA tensors: {path}")
print(f"[MeetMap SCAIL] Relighting safetensors validated: {len(state)} tensors.")
PY
  rm -f "$relight_sat"
  rmdir "$models_dir/loras/.meetmap_scail2_relighting/model" 2>/dev/null || true
  rmdir "$models_dir/loras/.meetmap_scail2_relighting" 2>/dev/null || true

  mkdir -p "$comfyui_dir/user/default/workflows"
  cp "$meetmap_dir/workflows/meetmap_scail2_character_swap_v2.json" \
     "$comfyui_dir/user/default/workflows/meetmap_scail2_character_swap_v2.json"
  cp "$meetmap_dir/workflows/meetmap_scail2_character_swap_v3_drive.json" \
     "$comfyui_dir/user/default/workflows/meetmap_scail2_character_swap_v3_drive.json"

  [[ -s "$comfyui_dir/user/default/workflows/meetmap_scail2_character_swap_v3_drive.json" ]] || {
    echo "V5 workflow copy failed." >&2
    exit 1
  }

  "$python_bin" - "$comfyui_dir/user/default/workflows/meetmap_scail2_character_swap_v3_drive.json" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
workflow = json.loads(path.read_text(encoding="utf-8"))
types = {node.get("type") for node in workflow.get("nodes", [])}
required = {
    "MeetMapGoogleDriveLatestVideo",
    "MeetMapSCAILReferenceBatch",
    "MeetMapSCAILLongVideoPlanner",
    "MeetMapSCAILChunkStitch",
    "MeetMapReleaseVRAMThenPassAudio",
    "SeedVCRun",
    "MeetMapGoogleDriveMarkProcessed",
}
missing = sorted(required - types)
if missing:
    raise SystemExit("Installed V5 workflow is missing required nodes: " + ", ".join(missing))

scail = next((n for n in workflow.get("nodes", []) if n.get("id") == 20), None)
if not scail:
    raise SystemExit("Installed V5 workflow is missing SCAIL subgraph node 20.")
widgets = scail.get("widgets_values", [])
if "scail2_relighting_lora_bf16.safetensors" not in widgets:
    raise SystemExit("Installed V5 workflow does not select the relighting LoRA.")

print("[MeetMap SCAIL] V5 workflow JSON validation passed.")
PY

  if [[ -s "$voice_ref" ]]; then
    current_sha="$(sha256sum "$voice_ref" | awk '{print $1}')"
    if [[ "$current_sha" != "$CREATOR_VOICE_SHA256" ]]; then
      echo "[MeetMap SCAIL] Removing stale creator voice reference with wrong SHA-256." >&2
      rm -f "$voice_ref"
    else
      echo "[MeetMap SCAIL] Creator voice reference present and verified: $voice_ref"
    fi
  fi

  if [[ ! -s "$voice_ref" ]]; then
    echo "[MeetMap SCAIL] Creator voice will be downloaded on the first workflow run from Drive file: ${MEETMAP_CREATOR_VOICE_DRIVE_FILE_ID:-$CREATOR_VOICE_DRIVE_FILE_ID}"
  fi

  echo "[MeetMap SCAIL] Installation complete."
  echo "[MeetMap SCAIL] V4 Drive + Seed-VC automation requires:"
  echo "  GOOGLE_SERVICE_ACCOUNT_JSON=<service account JSON secret>"
  echo "  MEETMAP_MOTION_DRIVE_FOLDER_ID=<folder id of MeetMap TikTok Content/Queue>"
  echo "  optional: MEETMAP_MOTION_EXPECTED_FOLDER_NAME=Queue"
  echo "  optional: MEETMAP_MOTION_EXPECTED_PARENT_FOLDER_NAME=MeetMap TikTok Content"
  echo "  optional: MEETMAP_MOTION_PROCESSED_FOLDER_ID=<override destination folder id>"
  echo "  optional: MEETMAP_MOTION_PROCESSED_FOLDER_NAME=Already posted"
  echo "  optional: MEETMAP_CREATOR_VOICE_DRIVE_FILE_ID=${CREATOR_VOICE_DRIVE_FILE_ID}"
  echo "  optional: MEETMAP_CREATOR_VOICE_SHA256=${CREATOR_VOICE_SHA256}"
  echo "[MeetMap SCAIL] The runtime refuses source folders outside MeetMap TikTok Content/Queue."
  echo "[MeetMap SCAIL] Successful sources are moved to sibling folder 'Already posted'."
  echo "[MeetMap SCAIL] Seed-VC uses the fixed creator_01 voice from Drive/Voice References."
  echo "[MeetMap SCAIL] Multi-reference: generated primary + face_front + face_angle + upper_body."
  echo "[MeetMap SCAIL] Long-video mode: native 81-frame chunks with 5-frame overlap."
  echo "[MeetMap SCAIL] Relighting LoRA: scail2_relighting_lora_bf16.safetensors."
  echo "[MeetMap SCAIL] VRAM policy: unload visual models before Seed-VC."
  echo "[MeetMap SCAIL] Share the parent folder 'MeetMap TikTok Content' with the service-account email as Editor."
  echo "[MeetMap SCAIL] Restart the Pod / ComfyUI process before loading the workflow."
  echo "[MeetMap SCAIL] Recommended workflow: meetmap_scail2_character_swap_v3_drive.json"
}

main "$@"
