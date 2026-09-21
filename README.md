# ComfyUI-MeetMap-UGC

MeetMap UGC custom nodes and the final RunPod workflow for standard ComfyUI installations.

This repository is intentionally designed **without a custom Docker image**. It targets recent normal ComfyUI releases and standard RunPod ComfyUI images. Compatibility is checked through capabilities such as `folder_paths.models_dir`, not by enforcing one exact ComfyUI version number.

## What it adds

- `MeetMapContentGenerator` — runs Qwen3 4B GGUF locally on CPU and generates the MeetMap topic, hook, German spoken script, image prompt, video prompt and duration.
- `MeetMapLTXPromptBuilder` — combines the video prompt and exact German dialogue for LTX 2.5.
- Impact Pack + Impact Subpack support for FaceDetailer.
- Automatic `face_yolov8m.pt` installation.
- Final workflow: `workflows/meetmap_ugc_realistic_runpod.json`.

The default Qwen model downloads automatically on the first Queue run if it is missing:

`bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF / Qwen_Qwen3-4B-Instruct-2507-Q5_K_M.gguf`

It is stored under:

`ComfyUI/models/LLM/Qwen_Qwen3-4B-Instruct-2507-Q5_K_M.gguf`

## RunPod setup

Use a normal ComfyUI RunPod image/template. No MeetMap-specific Docker image is required.

The repository is public, so no GitHub token is needed:

```bash
cd /workspace/ComfyUI/custom_nodes
if [ -d ComfyUI-MeetMap-UGC/.git ]; then
  git -C ComfyUI-MeetMap-UGC pull --ff-only
else
  git clone https://github.com/xxHalloxx/ComfyUI-MeetMap-UGC.git
fi
bash ComfyUI-MeetMap-UGC/install_runpod.sh
```

If your ComfyUI installation lives somewhere else, set `COMFYUI_DIR` first. The installer also detects common locations such as `/workspace/ComfyUI`, `/ComfyUI`, and `/opt/ComfyUI`.

Then restart ComfyUI.

The installer copies the final workflow to the recent-ComfyUI workflow folder when available:

`ComfyUI/user/default/workflows/meetmap_ugc_realistic_runpod.json`

The source remains in the repository at:

`workflows/meetmap_ugc_realistic_runpod.json`

## Model behavior

- **Qwen GGUF**: downloaded automatically by `MeetMapContentGenerator` on first use.
- **face_yolov8m.pt**: downloaded automatically by `install_runpod.sh` if missing.
- **Juggernaut XL v9**: the final workflow contains ComfyUI model-download metadata for `RunDiffusion/Juggernaut-XL-v9`.
- **LTX 2.5 models**: the existing official ComfyUI model-download metadata is preserved in the embedded LTX 2.5 subgraph.

Large model files are not committed to this repository.

## Compatibility

There is deliberately no check requiring exactly ComfyUI v0.36.0.

The installer:

- detects the actual ComfyUI directory,
- detects the active Python environment,
- verifies `folder_paths.models_dir`,
- preserves Impact Pack/Subpack versions already supplied by the image,
- uses a known workflow-compatible fallback revision only when those packages are absent.

This improves compatibility with multiple recent ComfyUI/RunPod images, but it cannot guarantee every historic or future ComfyUI version.

## Normal use

After installation/restart:

1. Open `meetmap_ugc_realistic_runpod.json`.
2. Allow ComfyUI to install/download missing workflow models when prompted.
3. Press Queue.
4. Qwen generates the MeetMap concept and German dialogue.
5. Juggernaut creates the UGC start image.
6. FaceDetailer refines the face.
7. LTX 2.5 generates the video/audio.
8. SaveVideo writes the MP4.

## Security

Never commit:

- GitHub tokens
- Hugging Face tokens
- RunPod API keys
- model files
- `.env` files

Public model downloads do not require secrets. If Hugging Face authentication is ever needed for a model, `huggingface_hub` supports the normal `HF_TOKEN` environment variable.
