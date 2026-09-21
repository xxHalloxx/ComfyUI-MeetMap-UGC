#!/usr/bin/env bash
# Provision MeetMap UGC v2 on a standard RunPod ComfyUI pod.
set -euo pipefail
unset PIP_CONSTRAINT

readonly MEETMAP_REPO_URL="https://github.com/xxHalloxx/ComfyUI-MeetMap-UGC.git"
readonly IMPACT_PACK_URL="https://github.com/ltdrdata/ComfyUI-Impact-Pack.git"
readonly IMPACT_PACK_COMMIT="429d0159ad429e64d2b3916e6e7be9c22d025c3c"
readonly IMPACT_SUBPACK_URL="https://github.com/ltdrdata/ComfyUI-Impact-Subpack.git"
readonly IMPACT_SUBPACK_COMMIT="50c7b71a6a224734cc9b21963c6d1926816a97f1"
readonly CROP_STITCH_URL="https://github.com/lquesada/ComfyUI-Inpaint-CropAndStitch.git"
readonly CROP_STITCH_COMMIT="bc4b1184b56c0ee25302ca755e43e264d0868998"

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

ensure_repo() {
  local url="$1" destination="$2" ref="${3:-}"
  if [[ -d "$destination/.git" ]]; then
    if [[ -n "$ref" ]]; then
      git -C "$destination" fetch --depth 1 origin "$ref"
      git -C "$destination" checkout --detach FETCH_HEAD
    else
      # MeetMap follows the repository default branch. Reset to the freshly
      # fetched remote HEAD so rerunning bootstrap cannot silently keep stale code.
      git -C "$destination" fetch --depth 1 origin main
      git -C "$destination" checkout -B main FETCH_HEAD
    fi
  elif [[ ! -e "$destination" ]]; then
    git clone --depth 1 "$url" "$destination"
    if [[ -n "$ref" ]]; then
      git -C "$destination" fetch --depth 1 origin "$ref"
      git -C "$destination" checkout --detach FETCH_HEAD
    fi
  elif [[ ! -f "$destination/__init__.py" ]]; then
    echo "Existing path is not a usable custom-node repository: $destination" >&2
    return 1
  fi
}

main() {
  [[ -n "${HF_TOKEN:-}" ]] || { echo "HF_TOKEN fehlt; v2 provisioning stops before downloads." >&2; exit 1; }

  local comfyui_dir python_bin custom_nodes meetmap_dir models_dir input_dir
  comfyui_dir="$(find_comfyui)"
  python_bin="$(find_python "$comfyui_dir")"
  custom_nodes="$comfyui_dir/custom_nodes"
  meetmap_dir="$custom_nodes/ComfyUI-MeetMap-UGC"
  models_dir="$comfyui_dir/models"
  input_dir="$comfyui_dir/input/meetmap_refs"
  mkdir -p "$custom_nodes" "$models_dir" "$input_dir"

  "$python_bin" - "$comfyui_dir" <<'PY'
import importlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
if not (root / "main.py").is_file() or not (root / "custom_nodes").is_dir():
    raise SystemExit("Required ComfyUI layout capability is missing.")
sys.path.insert(0, str(root))
folder_paths = importlib.import_module("folder_paths")
models_dir = getattr(folder_paths, "models_dir", None)
if not isinstance(models_dir, str) or not models_dir:
    raise SystemExit("Required folder_paths.models_dir capability is missing.")
nodes = importlib.import_module("nodes")
required_core = {
    "CheckpointLoaderSimple", "CLIPTextEncode", "KSampler", "VAEDecode", "ImageScale", "SaveVideo",
    "LoadImage", "PreviewImage", "SaveImage", "ImageScaleToTotalPixels", "VAEEncode", "ReferenceLatent",
    "UNETLoader", "CLIPLoader", "VAELoader", "Flux2Scheduler", "EmptyFlux2LatentImage",
    "SamplerCustomAdvanced", "CFGGuider", "RandomNoise", "KSamplerSelect", "MaskToImage",
}
missing = sorted(required_core - set(nodes.NODE_CLASS_MAPPINGS))
if missing:
    raise SystemExit("This ComfyUI installation lacks required capabilities: " + ", ".join(missing))
print("[MeetMap UGC] ComfyUI capability check passed.")
PY

  ensure_repo "$MEETMAP_REPO_URL" "$meetmap_dir"
  ensure_repo "$IMPACT_PACK_URL" "$custom_nodes/ComfyUI-Impact-Pack" "$IMPACT_PACK_COMMIT"
  ensure_repo "$IMPACT_SUBPACK_URL" "$custom_nodes/ComfyUI-Impact-Subpack" "$IMPACT_SUBPACK_COMMIT"
  ensure_repo "$CROP_STITCH_URL" "$custom_nodes/ComfyUI-Inpaint-CropAndStitch" "$CROP_STITCH_COMMIT"

  "$python_bin" -m pip install -r "$meetmap_dir/requirements.txt"
  "$python_bin" -m py_compile "$meetmap_dir/nodes.py" "$meetmap_dir/reference_nodes.py" "$meetmap_dir/__init__.py"
  [[ ! -f "$custom_nodes/ComfyUI-Impact-Pack/requirements.txt" ]] || "$python_bin" -m pip install -r "$custom_nodes/ComfyUI-Impact-Pack/requirements.txt"
  [[ ! -f "$custom_nodes/ComfyUI-Impact-Subpack/requirements.txt" ]] || "$python_bin" -m pip install -r "$custom_nodes/ComfyUI-Impact-Subpack/requirements.txt"

  if [[ -d "$meetmap_dir/refs" ]]; then
    mkdir -p "$input_dir"

    # Runtime paths intentionally omit the repository-only "creators/" level:
    # refs/creators/creator_01 -> ComfyUI/input/meetmap_refs/creator_01
    # This matches MeetMapReferenceFolderLoader's default path.
    if [[ -d "$meetmap_dir/refs/creators" ]]; then
      local creator_dir creator_name
      for creator_dir in "$meetmap_dir"/refs/creators/*; do
        [[ -d "$creator_dir" ]] || continue
        creator_name="$(basename "$creator_dir")"
        mkdir -p "$input_dir/$creator_name"
        cp -a "$creator_dir/." "$input_dir/$creator_name/"
      done
    fi

    if [[ -d "$meetmap_dir/refs/style" ]]; then
      mkdir -p "$input_dir/style"
      cp -a "$meetmap_dir/refs/style/." "$input_dir/style/"
    fi
  fi

  "$python_bin" - "$models_dir" <<'PY'
from pathlib import Path
import shutil
import sys
from huggingface_hub import hf_hub_download

models = Path(sys.argv[1])
specs = [
    ("bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF", "Qwen_Qwen3-4B-Instruct-2507-Q5_K_M.gguf", models / "LLM/Qwen_Qwen3-4B-Instruct-2507-Q5_K_M.gguf"),
    ("Bingsu/adetailer", "face_yolov8m.pt", models / "ultralytics/bbox/face_yolov8m.pt"),
    ("black-forest-labs/FLUX.2-klein-4b-fp8", "flux-2-klein-4b-fp8.safetensors", models / "diffusion_models/flux-2-klein-4b-fp8.safetensors"),
    ("Comfy-Org/z_image_turbo", "split_files/text_encoders/qwen_3_4b.safetensors", models / "text_encoders/qwen_3_4b.safetensors"),
    ("Comfy-Org/flux2-dev", "split_files/vae/flux2-vae.safetensors", models / "vae/flux2-vae.safetensors"),
    ("Lightricks/LTX-2.5", "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors", models / "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"),
    ("Lightricks/LTX-2.5", "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors", models / "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"),
    ("Lightricks/LTX-2.5", "vae/ltx-2.5-video-vae-bf16.safetensors", models / "vae/ltx-2.5-video-vae-bf16.safetensors"),
    ("Lightricks/LTX-2.5", "vae/ltx-2.5-audio-vae-bf16.safetensors", models / "vae/ltx-2.5-audio-vae-bf16.safetensors"),
    ("Lightricks/LTX-2.5", "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors", models / "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"),
    ("Comfy-Org/gemma-4", "text_encoders/gemma4_e2b_it_int8_convrot.safetensors", models / "text_encoders/gemma4_e2b_it_int8_convrot.safetensors"),
]
for repo, filename, target in specs:
    if target.is_file() and target.stat().st_size > 0:
        print(f"[MeetMap UGC] present: {target}")
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    downloaded = Path(hf_hub_download(repo_id=repo, filename=filename, local_dir=str(target.parent)))
    if downloaded.resolve() != target.resolve():
        shutil.copyfile(downloaded, target)
        try:
            downloaded.unlink()
        except OSError:
            pass
    if not target.is_file() or target.stat().st_size <= 0:
        raise SystemExit(f"Model download failed: {target}")
    print(f"[MeetMap UGC] downloaded: {target}")
PY

  mkdir -p "$comfyui_dir/user/default/workflows"
  cp "$meetmap_dir/workflows/meetmap_ugc_v2.json" "$comfyui_dir/user/default/workflows/meetmap_ugc_v2.json"
  cp "$meetmap_dir/workflows/meetmap_ugc_realistic_runpod.json" "$comfyui_dir/user/default/workflows/meetmap_ugc_realistic_runpod.json"

  local creator_ref_count required_file
  creator_ref_count="$(find "$input_dir/creator_01" -maxdepth 1 -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.webp' \) -size +0c | wc -l | tr -d ' ')"
  [[ "${creator_ref_count:-0}" -ge 1 ]] || { echo "Pod provisioning incomplete: no creator reference images found in $input_dir/creator_01" >&2; exit 1; }
  echo "[MeetMap UGC] Creator reference pool: $creator_ref_count image(s). Workflow default max: 12."

  for required_file in \
    "$models_dir/LLM/Qwen_Qwen3-4B-Instruct-2507-Q5_K_M.gguf" \
    "$models_dir/ultralytics/bbox/face_yolov8m.pt" \
    "$models_dir/diffusion_models/flux-2-klein-4b-fp8.safetensors" \
    "$models_dir/text_encoders/qwen_3_4b.safetensors" \
    "$models_dir/vae/flux2-vae.safetensors" \
    "$models_dir/diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors" \
    "$models_dir/text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors" \
    "$models_dir/vae/ltx-2.5-video-vae-bf16.safetensors" \
    "$models_dir/vae/ltx-2.5-audio-vae-bf16.safetensors" \
    "$models_dir/latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors" \
    "$models_dir/text_encoders/gemma4_e2b_it_int8_convrot.safetensors"; do
    [[ -s "$required_file" ]] || { echo "Pod provisioning incomplete: missing $required_file" >&2; exit 1; }
  done

  [[ -s "$custom_nodes/ComfyUI-Inpaint-CropAndStitch/inpaint_cropandstitch.py" ]] || { echo "Pod provisioning incomplete: Crop & Stitch custom node missing" >&2; exit 1; }
  echo "[MeetMap UGC] Reddit-style face realism nodes installed: Impact YOLO + Crop & Stitch + FLUX.2 edit."
  echo "[MeetMap UGC] ALL REQUIRED MODELS ARE ON THIS POD."
  echo "[MeetMap UGC] Installation complete. Restart ComfyUI and import meetmap_ugc_v2.json."
}

main "$@"
