#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_SRC="${SCRIPT_DIR}/workflows/meetmap_ugc_realistic_runpod.json"

detect_comfyui() {
  if [[ -n "${COMFYUI_DIR:-}" && -f "${COMFYUI_DIR}/main.py" ]]; then
    return
  fi

  local inferred
  inferred="$(cd "${SCRIPT_DIR}/../.." 2>/dev/null && pwd || true)"
  COMFYUI_DIR=""

  for candidate in "${inferred}" /workspace/ComfyUI /ComfyUI /opt/ComfyUI; do
    if [[ -n "${candidate}" && -f "${candidate}/main.py" ]]; then
      COMFYUI_DIR="${candidate}"
      return
    fi
  done
}

detect_python() {
  if [[ -x "${COMFYUI_DIR}/venv/bin/python" ]]; then
    PYTHON_BIN="${COMFYUI_DIR}/venv/bin/python"
  elif [[ -x "${COMFYUI_DIR}/.venv/bin/python" ]]; then
    PYTHON_BIN="${COMFYUI_DIR}/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3 || command -v python)"
  fi
}

detect_comfyui

if [[ -z "${COMFYUI_DIR:-}" || ! -f "${COMFYUI_DIR}/main.py" ]]; then
  echo "ComfyUI not found. Set COMFYUI_DIR to the ComfyUI installation directory." >&2
  exit 1
fi

detect_python

echo "[MeetMap UGC] ComfyUI: ${COMFYUI_DIR}"
echo "[MeetMap UGC] Python: ${PYTHON_BIN}"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required but is not installed." >&2
  exit 1
fi

# Capability detection instead of an exact ComfyUI version lock.
PYTHONPATH="${COMFYUI_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "${PYTHON_BIN}" - <<'PY'
import os
import folder_paths

models_dir = getattr(folder_paths, "models_dir", None)
if not models_dir:
    raise SystemExit("ComfyUI capability check failed: folder_paths.models_dir is unavailable.")
if not os.path.isdir(os.path.dirname(models_dir)):
    raise SystemExit(f"ComfyUI capability check failed: invalid models_dir {models_dir!r}.")
print(f"[MeetMap UGC] Capability check OK. models_dir={models_dir}")
PY

# llama-cpp-python may compile from source when no compatible wheel exists.
if ! command -v cmake >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1 && [[ "$(id -u)" -eq 0 ]]; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends build-essential cmake
  rm -rf /var/lib/apt/lists/*
fi

mkdir -p   "${COMFYUI_DIR}/custom_nodes"   "${COMFYUI_DIR}/models/LLM"   "${COMFYUI_DIR}/models/ultralytics/bbox"

# Install this node package's runtime dependencies. Qwen remains CPU-only.
CMAKE_ARGS="-DGGML_CUDA=OFF" FORCE_CMAKE=1   "${PYTHON_BIN}" -m pip install --no-cache-dir -r "${SCRIPT_DIR}/requirements.txt"

ensure_repo() {
  local repo_url="$1"
  local fallback_ref="$2"
  local destination="$3"

  # Preserve a version already supplied by the RunPod/ComfyUI image. This is
  # intentionally more version-tolerant than forcing one old revision.
  if [[ -d "${destination}" ]]; then
    echo "[MeetMap UGC] Existing custom node kept: ${destination}"
    return
  fi

  git clone --filter=blob:none "${repo_url}" "${destination}"

  # Use a known workflow-compatible revision only for a fresh install.
  if [[ -n "${fallback_ref}" ]]; then
    if git -C "${destination}" fetch --depth 1 origin "${fallback_ref}" >/dev/null 2>&1; then
      git -C "${destination}" checkout --detach "${fallback_ref}"
    else
      echo "[MeetMap UGC] Warning: could not fetch pinned fallback ${fallback_ref}; keeping cloned default branch." >&2
    fi
  fi
}

ensure_repo   "https://github.com/ltdrdata/ComfyUI-Impact-Pack.git"   "429d0159ad429e64d2b3916e6e7be9c22d025c3c"   "${COMFYUI_DIR}/custom_nodes/ComfyUI-Impact-Pack"

ensure_repo   "https://github.com/ltdrdata/ComfyUI-Impact-Subpack.git"   "50c7b71a6a224734cc9b21963c6d1926816a97f1"   "${COMFYUI_DIR}/custom_nodes/ComfyUI-Impact-Subpack"

if [[ -f "${COMFYUI_DIR}/custom_nodes/ComfyUI-Impact-Pack/requirements.txt" ]]; then
  "${PYTHON_BIN}" -m pip install --no-cache-dir -r "${COMFYUI_DIR}/custom_nodes/ComfyUI-Impact-Pack/requirements.txt"
fi

if [[ -f "${COMFYUI_DIR}/custom_nodes/ComfyUI-Impact-Subpack/requirements.txt" ]]; then
  "${PYTHON_BIN}" -m pip install --no-cache-dir -r "${COMFYUI_DIR}/custom_nodes/ComfyUI-Impact-Subpack/requirements.txt"
fi

# UltralyticsDetectorProvider references this file, but its workflow schema does
# not reliably carry ComfyUI model-download metadata across versions. Install
# the small detector explicitly as a version-independent fallback.
FACE_MODEL="${COMFYUI_DIR}/models/ultralytics/bbox/face_yolov8m.pt"
if [[ ! -s "${FACE_MODEL}" ]]; then
  echo "[MeetMap UGC] Downloading face_yolov8m.pt ..."
  "${PYTHON_BIN}" - "${FACE_MODEL}" <<'PY'
import os
import sys
from huggingface_hub import hf_hub_download

target = os.path.abspath(sys.argv[1])
target_dir = os.path.dirname(target)
os.makedirs(target_dir, exist_ok=True)

path = hf_hub_download(
    repo_id="Bingsu/adetailer",
    filename="face_yolov8m.pt",
    local_dir=target_dir,
)
if not os.path.isfile(path) or os.path.getsize(path) <= 0:
    raise SystemExit(f"face_yolov8m.pt download failed: {path}")
PY
else
  echo "[MeetMap UGC] Face detector already present."
fi

# Gated LTX 2.5 files: ComfyUI may show these with orange lock icons.
# If HF_TOKEN is available, download them directly so the workflow does not
# depend on the frontend downloader being authenticated.
# Public workflow models are installed directly as well. This removes the
# need to use ComfyUI's manual "Download to Pod" model picker.
echo "[MeetMap UGC] Ensuring public workflow models are installed..."
PYTHONPATH="${COMFYUI_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "${PYTHON_BIN}" - "${COMFYUI_DIR}" <<'PY'
import os
import sys
from huggingface_hub import hf_hub_download

comfyui_dir = os.path.abspath(sys.argv[1])
models_dir = os.path.join(comfyui_dir, "models")

required = [
    (
        "RunDiffusion/Juggernaut-XL-v9",
        "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors",
        os.path.join(models_dir, "checkpoints", "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"),
        os.path.join(models_dir, "checkpoints"),
    ),
    (
        "Comfy-Org/gemma-4",
        "text_encoders/gemma4_e2b_it_int8_convrot.safetensors",
        os.path.join(models_dir, "text_encoders", "gemma4_e2b_it_int8_convrot.safetensors"),
        models_dir,
    ),
]

for repo_id, repo_path, expected, local_dir in required:
    if os.path.isfile(expected) and os.path.getsize(expected) > 0:
        print(f"[MeetMap UGC] Already present: {expected}")
        continue

    print(f"[MeetMap UGC] Downloading public model: {repo_id}/{repo_path}")
    os.makedirs(os.path.dirname(expected), exist_ok=True)
    downloaded = hf_hub_download(
        repo_id=repo_id,
        filename=repo_path,
        local_dir=local_dir,
    )

    if not os.path.isfile(expected) or os.path.getsize(expected) <= 0:
        raise SystemExit(
            f"[MeetMap UGC] Download returned {downloaded!r}, but expected file is missing: {expected}"
        )
PY

if [[ -n "${HF_TOKEN:-}" ]]; then
  echo "[MeetMap UGC] HF_TOKEN detected. Ensuring gated LTX 2.5 models are installed..."
  PYTHONPATH="${COMFYUI_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "${PYTHON_BIN}" - "${COMFYUI_DIR}" <<'PY'
import os
import sys
from huggingface_hub import hf_hub_download

comfyui_dir = os.path.abspath(sys.argv[1])
models_dir = os.path.join(comfyui_dir, "models")
token = os.environ.get("HF_TOKEN")

required = [
    ("diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
     os.path.join(models_dir, "diffusion_models", "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors")),
    ("vae/ltx-2.5-video-vae-bf16.safetensors",
     os.path.join(models_dir, "vae", "ltx-2.5-video-vae-bf16.safetensors")),
    ("vae/ltx-2.5-audio-vae-bf16.safetensors",
     os.path.join(models_dir, "vae", "ltx-2.5-audio-vae-bf16.safetensors")),
    ("text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
     os.path.join(models_dir, "text_encoders", "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors")),
    ("latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
     os.path.join(models_dir, "latent_upscale_models", "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors")),
]

for repo_path, expected in required:
    if os.path.isfile(expected) and os.path.getsize(expected) > 0:
        print(f"[MeetMap UGC] Already present: {expected}")
        continue

    print(f"[MeetMap UGC] Downloading gated LTX model: {repo_path}")
    os.makedirs(os.path.dirname(expected), exist_ok=True)
    try:
        downloaded = hf_hub_download(
            repo_id="Lightricks/LTX-2.5",
            filename=repo_path,
            local_dir=models_dir,
            token=token,
        )
    except Exception as exc:
        raise SystemExit(
            "\n[MeetMap UGC] LTX 2.5 download failed. "
            "Make sure HF_TOKEN belongs to the Hugging Face account that accepted "
            "the Lightricks/LTX-2.5 license/access agreement.\n"
            f"Failed file: {repo_path}\nError: {exc}"
        )

    if not os.path.isfile(expected) or os.path.getsize(expected) <= 0:
        raise SystemExit(
            f"[MeetMap UGC] Download returned {downloaded!r}, but expected file is missing: {expected}"
        )
PY
else
  echo "[MeetMap UGC] HF_TOKEN is not set."
  echo "[MeetMap UGC] Gated LTX 2.5 models may appear locked in ComfyUI until HF_TOKEN is added to the RunPod environment."
fi

# Put the final workflow into the normal recent-ComfyUI user workflow folder.
# This is best-effort; the source always remains in this repository as well.
if [[ -f "${WORKFLOW_SRC}" ]]; then
  WORKFLOW_DIR="${COMFYUI_DIR}/user/default/workflows"
  mkdir -p "${WORKFLOW_DIR}"
  cp "${WORKFLOW_SRC}" "${WORKFLOW_DIR}/meetmap_ugc_realistic_runpod.json"
  echo "[MeetMap UGC] Workflow installed: ${WORKFLOW_DIR}/meetmap_ugc_realistic_runpod.json"
else
  echo "[MeetMap UGC] Warning: final workflow JSON is missing from the repository." >&2
fi

# Static package checks only; no GPU inference is triggered.
"${PYTHON_BIN}" -m py_compile "${SCRIPT_DIR}/nodes.py" "${SCRIPT_DIR}/__init__.py"

echo
echo "[MeetMap UGC] Installation complete."
echo "[MeetMap UGC] Restart ComfyUI if it is already running."
echo "[MeetMap UGC] Open meetmap_ugc_realistic_runpod.json."
echo "[MeetMap UGC] Qwen downloads automatically on the first Queue run if missing."
echo "[MeetMap UGC] Juggernaut, face detector, prompt enhancer and LTX models are installed directly when possible."
