#!/usr/bin/env bash
# Install models + MeetMap runtime support for SCAIL-2 character and Seed-VC voice swap.
set -euo pipefail
unset PIP_CONSTRAINT

readonly MEETMAP_REPO_URL="https://github.com/xxHalloxx/ComfyUI-MeetMap-UGC.git"
readonly SEEDVC_REPO_URL="https://github.com/billwuhao/ComfyUI_Seed-VC.git"
readonly SEEDVC_COMMIT="02c0cb8b05121dd9e391b4287c8e28e0eb4e79a4"
readonly CREATOR_VOICE_DRIVE_FILE_ID="1BjZUeye3fVAkA1DtvdlYdsNQzMY_gANt"
readonly CREATOR_VOICE_SHA256="e0c502c490c74bbda5226fae8fb95206bb6facf1eb2d1d2ff025b19f9ae62fb7"
readonly SCAIL2_RELIGHT_SHA256="538056c306179bb82e88f8e965609e7ebf99fbf2caa8b9e49db6a718974c3d7b"

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
  local comfyui_dir python_bin custom_nodes meetmap_dir seedvc_dir models_dir input_dir voice_ref seedvc_available
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
  seedvc_available=0
  if ensure_seedvc_repo "$seedvc_dir"; then
    seedvc_available=1
  elif [[ -f "$seedvc_dir/seedvcnode.py" ]]; then
    echo "[MeetMap SCAIL] WARNING: Seed-VC update failed; keeping existing local checkout." >&2
    seedvc_available=1
  else
    echo "[MeetMap SCAIL] WARNING: Seed-VC repository unavailable; original-audio fallback remains available." >&2
  fi

  if ! "$python_bin" -c "import llama_cpp, huggingface_hub, googleapiclient, soundfile; from google.oauth2 import service_account" >/dev/null 2>&1; then
    "$python_bin" -m pip install -r "$meetmap_dir/requirements.txt"
  fi

  if [[ "$seedvc_available" == "1" && -f "$seedvc_dir/requirements.txt" ]]; then
    echo "[MeetMap SCAIL] Installing pinned Seed-VC runtime dependencies..."
    if ! "$python_bin" -m pip install -r "$seedvc_dir/requirements.txt"; then
      echo "[MeetMap SCAIL] WARNING: Seed-VC dependency install failed; original-audio fallback remains available." >&2
    fi
  else
    echo "[MeetMap SCAIL] Seed-VC dependency install skipped."
  fi

  if ! command -v ffmpeg >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1 && [[ "$(id -u)" == "0" ]]; then
      echo "[MeetMap SCAIL] ffmpeg missing; installing system fallback decoder..."
      apt-get update -qq
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg
    else
      echo "[MeetMap SCAIL] WARNING: ffmpeg missing; torchaudio/soundfile fallbacks remain available." >&2
    fi
  fi

  "$python_bin" -m py_compile \
    "$meetmap_dir/nodes.py" \
    "$meetmap_dir/reference_nodes.py" \
    "$meetmap_dir/gdrive_nodes.py" \
    "$meetmap_dir/voice_nodes.py" \
    "$meetmap_dir/scail_runtime_nodes.py" \
    "$meetmap_dir/output_nodes.py" \
    "$meetmap_dir/tools/convert_scail2_lora.py" \
    "$meetmap_dir/tools/validate_meetmap_workflow.py" \
    "$meetmap_dir/__init__.py"

  if [[ -f "$seedvc_dir/seedvcnode.py" ]]; then
    if ! "$python_bin" -m py_compile "$seedvc_dir/seedvcnode.py"; then
      echo "[MeetMap SCAIL] WARNING: Seed-VC node compile failed; original-audio fallback remains available." >&2
    fi
  fi

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
seedvc_ok = (
    seedvc_node.is_file()
    and "class SeedVCRun" in seedvc_node.read_text(encoding="utf-8", errors="ignore")
)

if missing:
    raise SystemExit(
        "ComfyUI/runtime is missing required SCAIL-2 core support: "
        + "; ".join(missing)
    )
if seedvc_ok:
    print("[MeetMap SCAIL] ComfyUI SCAIL core + Seed-VC capability check passed.")
else:
    print("[MeetMap SCAIL] WARNING: Seed-VC runtime unavailable; original-audio fallback will be used.")
PY

  echo "[MeetMap SCAIL] Downloading SCAIL-2 + FLUX.2 + Seed-VC model set..."
  "$python_bin" - "$models_dir" <<'PY'
import os
from pathlib import Path
import shutil
import sys
from huggingface_hub import hf_hub_download
from safetensors import safe_open

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

    # Comfy-Org publishes the official SCAIL-2 relighting LoRA already converted
    # to the safetensors format used by ComfyUI.
    ("Comfy-Org/SCAIL-2", "loras/wan2.1_SCAIL_2_relight_lora_bf16.safetensors",
     models / "loras/wan2.1_SCAIL_2_relight_lora_bf16.safetensors"),

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

def optional_target(path):
    rel = path.relative_to(models).as_posix()
    optional_exact = {
        "loras/wan2.1_SCAIL_2_DPO_lora_bf16.safetensors",
        "loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
        "loras/wan2.1_SCAIL_2_relight_lora_bf16.safetensors",
    }
    return rel.startswith("TTS/") or rel in optional_exact


def valid_existing(path):
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    if path.suffix.lower() != ".safetensors":
        return True
    try:
        with safe_open(str(path), framework="pt", device="cpu") as handle:
            keys = list(handle.keys())
        if not keys:
            raise RuntimeError("no tensors")
        return True
    except Exception as exc:
        print(f"[MeetMap SCAIL] removing invalid safetensors {path}: {exc}")
        path.unlink(missing_ok=True)
        return False


for repo, filename, target in specs:
    if valid_existing(target):
        print(f"[MeetMap SCAIL] verified present: {target}")
        continue

    target.parent.mkdir(parents=True, exist_ok=True)
    last_error = None
    for attempt in range(1, 4):
        try:
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
            if not valid_existing(target):
                raise RuntimeError(f"downloaded file did not validate: {target}")
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            print(f"[MeetMap SCAIL] download attempt {attempt}/3 failed for {repo}/{filename}: {exc}")
            if attempt < 3:
                import time
                time.sleep(2 ** (attempt - 1))

    if last_error is not None:
        if optional_target(target):
            target.unlink(missing_ok=True)
            print(
                f"[MeetMap SCAIL] WARNING: optional model unavailable after 3 attempts: "
                f"{target}: {last_error}. Continuing with runtime fallback."
            )
            continue
        raise SystemExit(f"Required model download failed after 3 attempts: {target}: {last_error}")
    print(f"[MeetMap SCAIL] downloaded + verified: {target}")
PY

  # Relighting is optional. Invalid/missing copies are removed so the workflow
  # cleanly falls back to the base SCAIL model.
  relight_out="$models_dir/loras/wan2.1_SCAIL_2_relight_lora_bf16.safetensors"
  if [[ -s "$relight_out" ]]; then
    actual_relight_sha="$(sha256sum "$relight_out" | awk '{print $1}')"
    if [[ "$actual_relight_sha" != "$SCAIL2_RELIGHT_SHA256" ]]; then
      echo "[MeetMap SCAIL] WARNING: relighting LoRA checksum mismatch; continuing without relighting." >&2
      rm -f "$relight_out"
    else
      echo "[MeetMap SCAIL] Relighting LoRA checksum verified."
    fi
  else
    echo "[MeetMap SCAIL] WARNING: relighting LoRA unavailable; continuing without relighting." >&2
  fi
  rm -rf "$models_dir/loras/.meetmap_scail2_relighting"

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
    "MeetMapSeedVCWithFallback",
    "MeetMapSafeSaveVideo",
    "MeetMapGoogleDriveFinalizeSafe",
}
missing = sorted(required - types)
if missing:
    raise SystemExit("Installed V5 workflow is missing required nodes: " + ", ".join(missing))

scail = next((n for n in workflow.get("nodes", []) if n.get("id") == 20), None)
if not scail:
    raise SystemExit("Installed V5 workflow is missing SCAIL subgraph node 20.")
widgets = scail.get("widgets_values", [])
if "wan2.1_SCAIL_2_relight_lora_bf16.safetensors" not in widgets:
    raise SystemExit("Installed V5 workflow does not select the preferred relighting LoRA.")

subgraphs = ((workflow.get("definitions") or {}).get("subgraphs") or [])
scail_graph = next(
    (graph for graph in subgraphs if graph.get("id") == "ab27c382-c076-424b-b976-d1bc3f88ba12"),
    None,
)
if not scail_graph:
    raise SystemExit("Installed V5 workflow is missing the SCAIL subgraph definition.")
subgraph_types = {node.get("type") for node in scail_graph.get("nodes", [])}
optional_lora_count = sum(
    1 for node in scail_graph.get("nodes", [])
    if node.get("type") == "MeetMapOptionalLoraModelLoader"
)
if optional_lora_count < 3:
    raise SystemExit(
        f"Installed V5 workflow needs 3 optional LoRA fallback loaders, found {optional_lora_count}."
    )

voice = next((n for n in workflow.get("nodes", []) if n.get("id") == 36), None)
if not voice or voice.get("type") != "MeetMapSeedVCWithFallback":
    raise SystemExit("Installed V5 workflow is missing Seed-VC recovery wrapper.")

print("[MeetMap SCAIL] V5 workflow fallback validation passed.")
PY

  if [[ -n "${GOOGLE_SERVICE_ACCOUNT_JSON:-}" ]]; then
    echo "[MeetMap SCAIL] Running Google Drive permission + creator voice preflight..."
    if ! "$python_bin" - "$voice_ref" <<'PY'
import hashlib
import io
import json
import os
from pathlib import Path
import sys

import soundfile as sf
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

QUEUE_ID = "1oVcbIvv3w3FJK6VTunFzKMW4QRx4UIxf"
PROCESSED_ID = "1hbZ44M0_TVxdyho4F-HxkQvPw_rkonB6"
PARENT_ID = "1zqe7b5Nx06cJRE6MgaWomhRAv3rbZKAi"
VOICE_ID = "1BjZUeye3fVAkA1DtvdlYdsNQzMY_gANt"
VOICE_SHA = "e0c502c490c74bbda5226fae8fb95206bb6facf1eb2d1d2ff025b19f9ae62fb7"
SCOPE = "https://www.googleapis.com/auth/drive"

raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
try:
    info = json.loads(raw)
    if isinstance(info, str):
        info = json.loads(info)
except Exception as exc:
    raise SystemExit(f"GOOGLE_SERVICE_ACCOUNT_JSON is invalid: {exc}")

creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
service = build("drive", "v3", credentials=creds, cache_discovery=False)

queue = service.files().get(
    fileId=QUEUE_ID,
    fields="id,name,mimeType,parents",
    supportsAllDrives=True,
).execute(num_retries=3)
if queue.get("name") != "Queue":
    raise SystemExit(f"Drive preflight: Queue ID resolved to unexpected name {queue.get('name')!r}")
parents = [str(x) for x in (queue.get("parents") or [])]
if PARENT_ID not in parents:
    raise SystemExit(
        "Drive preflight: service account can see Queue but not its expected parent "
        "MeetMap TikTok Content. Share the parent folder with the service-account email as Editor."
    )

parent = service.files().get(
    fileId=PARENT_ID,
    fields="id,name,mimeType",
    supportsAllDrives=True,
).execute(num_retries=3)
if parent.get("name") != "MeetMap TikTok Content":
    raise SystemExit("Drive preflight: unexpected parent folder name.")

processed = service.files().get(
    fileId=PROCESSED_ID,
    fields="id,name,mimeType,parents",
    supportsAllDrives=True,
).execute(num_retries=3)
if processed.get("name") != "Already posted":
    raise SystemExit("Drive preflight: processed folder ID is not 'Already posted'.")

voice_meta = service.files().get(
    fileId=VOICE_ID,
    fields="id,name,mimeType,size",
    supportsAllDrives=True,
).execute(num_retries=3)

target = Path(sys.argv[1])
target.parent.mkdir(parents=True, exist_ok=True)

def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

needs_download = not target.is_file() or sha256(target) != VOICE_SHA
if needs_download:
    target.unlink(missing_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    partial.unlink(missing_ok=True)
    request = service.files().get_media(fileId=VOICE_ID, supportsAllDrives=True)
    with partial.open("wb") as handle:
        downloader = MediaIoBaseDownload(handle, request, chunksize=4 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk(num_retries=3)
    expected_size = int(voice_meta.get("size") or 0)
    if expected_size and partial.stat().st_size != expected_size:
        partial.unlink(missing_ok=True)
        raise SystemExit("Drive preflight: creator voice download size mismatch.")
    partial.replace(target)

if sha256(target) != VOICE_SHA:
    target.unlink(missing_ok=True)
    raise SystemExit("Drive preflight: creator voice SHA-256 mismatch.")

audio_info = sf.info(str(target))
if audio_info.frames <= 0 or audio_info.samplerate <= 0:
    raise SystemExit("Drive preflight: creator voice cannot be decoded by soundfile.")

print(
    "[MeetMap SCAIL] Drive preflight passed: Queue + parent + Already posted + "
    f"creator voice ({audio_info.frames / audio_info.samplerate:.2f}s) are accessible."
)
PY
    then
      echo "[MeetMap SCAIL] WARNING: Drive preflight failed; installation continues. Fix Drive access before a Drive-sourced render." >&2
    fi
  else
    echo "[MeetMap SCAIL] WARNING: GOOGLE_SERVICE_ACCOUNT_JSON is not set; Drive preflight skipped." >&2
    echo "[MeetMap SCAIL] The workflow can still start later if the secret is injected before ComfyUI runs." >&2
  fi

  if [[ -s "$voice_ref" ]]; then
    if ! "$python_bin" - "$voice_ref" <<'PY'
from pathlib import Path
import sys
import soundfile as sf

path = Path(sys.argv[1])
info = sf.info(str(path))
if info.frames <= 0 or info.samplerate <= 0:
    raise SystemExit(f"Creator voice FLAC failed libsndfile validation: {path}")
print(
    f"[MeetMap SCAIL] Voice decoder self-test passed: "
    f"{info.frames / info.samplerate:.2f}s @ {info.samplerate} Hz."
)
PY
    then
      echo "[MeetMap SCAIL] WARNING: local creator voice failed decoder self-test; removing it for runtime redownload/fallback." >&2
      rm -f "$voice_ref"
    fi
  fi

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
  echo "[MeetMap SCAIL] Voice recovery: torchaudio -> soundfile -> ffmpeg; Seed-VC retries once then falls back to original audio."
  echo "[MeetMap SCAIL] Multi-reference: generated primary + face_front + face_angle + upper_body."
  echo "[MeetMap SCAIL] Long-video mode: native 81-frame chunks with 5-frame overlap."
  echo "[MeetMap SCAIL] Relighting is optional at runtime: preferred LoRA is used when healthy, otherwise base SCAIL continues."
  echo "[MeetMap SCAIL] VRAM policy: unload visual models before Seed-VC."
  echo "[MeetMap SCAIL] Share the parent folder 'MeetMap TikTok Content' with the service-account email as Editor."
  echo "[MeetMap SCAIL] Restart the Pod / ComfyUI process before loading the workflow."
  echo "[MeetMap SCAIL] Recommended workflow: meetmap_scail2_character_swap_v3_drive.json"
}

main "$@"
