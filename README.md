# ComfyUI-MeetMap-UGC

Small ComfyUI custom-node package for the MeetMap UGC workflow.

It adds:

- `MeetMapContentGenerator` — runs Qwen3 4B GGUF locally on CPU and generates the MeetMap topic, hook, German spoken script, image prompt, video prompt and duration.
- `MeetMapLTXPromptBuilder` — combines the video prompt and exact German dialogue into the prompt used by the LTX 2.5 section of the workflow.

The default Qwen model is downloaded automatically from Hugging Face on the first Queue run if it is missing:

`bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF / Qwen_Qwen3-4B-Instruct-2507-Q5_K_M.gguf`

It is stored at:

`ComfyUI/models/LLM/Qwen_Qwen3-4B-Instruct-2507-Q5_K_M.gguf`

## RunPod setup

This repository is designed to be installed on top of a normal ComfyUI RunPod image. You do **not** need a custom Docker image just for these nodes.

Clone this repository into ComfyUI's `custom_nodes` directory, then run:

```bash
bash ComfyUI-MeetMap-UGC/install_runpod.sh
```

The installer:

1. Finds the active ComfyUI installation.
2. Installs `llama-cpp-python` in CPU-only mode.
3. Installs `huggingface_hub`.
4. Installs the pinned Impact Pack and Impact Subpack versions needed by FaceDetailer.
5. Creates the `models/LLM` directory.
6. Leaves the large workflow models to the normal ComfyUI/model-download flow.
7. Lets the MeetMap node download only its Qwen GGUF automatically on first use.

After installation, restart ComfyUI and import the MeetMap workflow JSON.

## Public repository clone

If this repository is public:

```bash
cd /workspace/ComfyUI/custom_nodes
git clone https://github.com/xxHalloxx/ComfyUI-MeetMap-UGC.git
bash ComfyUI-MeetMap-UGC/install_runpod.sh
```

## Private repository clone

The repository is currently private. For RunPod, store a fine-grained GitHub token with **read-only Contents access to this repository** as a secret environment variable named `GITHUB_TOKEN`.

Then:

```bash
cd /workspace/ComfyUI/custom_nodes
git -c 'credential.helper=!f() { echo username=x-access-token; echo password=$GITHUB_TOKEN; }; f' \
  clone https://github.com/xxHalloxx/ComfyUI-MeetMap-UGC.git
bash ComfyUI-MeetMap-UGC/install_runpod.sh
```

Do not paste the token directly into the command or commit it to this repository.

## Optional direct startup

If your RunPod template expects you to start ComfyUI manually after cloning the repo, you can use:

```bash
bash /workspace/ComfyUI/custom_nodes/ComfyUI-MeetMap-UGC/start_runpod.sh
```

This installs the dependencies and then starts ComfyUI on `0.0.0.0:8188`.

If the standard RunPod image already manages the ComfyUI process itself, use only `install_runpod.sh` and restart ComfyUI through the template instead of launching a second ComfyUI process.

## Runtime behavior

- Qwen uses `n_gpu_layers=0`, so it does not reserve GPU VRAM.
- The content node re-runs on each normal Queue execution.
- If Qwen returns malformed JSON, the node performs one low-temperature retry.
- The LTX prompt builder does no AI inference.
- No API keys are required by these custom nodes for inference.
- If Hugging Face ever requires authentication for a model download, the standard `HF_TOKEN` environment variable is supported by `huggingface_hub`.

## Security

Never commit:

- GitHub tokens
- Hugging Face tokens
- RunPod API keys
- model files
- `.env` files
