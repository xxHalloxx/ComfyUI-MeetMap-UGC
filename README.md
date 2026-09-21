# ComfyUI MeetMap UGC

Local custom nodes and RunPod bootstrap for MeetMap UGC v1/v2:

- `MeetMapContentGenerator` performs one CPU-only Qwen GGUF inference. If the documented default Qwen file is missing, it downloads the public file into `ComfyUI/models/LLM/` using normal Hugging Face environment authentication when available.
- `MeetMapLTXPromptBuilder` deterministically combines the video prompt with the exact German dialogue for LTX-2.5.
- `MeetMapLTXRelayPromptBuilder` deterministically turns Qwen acting beats into a time-segmented LTX relay prompt.

Designed for recent ComfyUI versions and standard RunPod ComfyUI images. The installer uses capability detection rather than enforcing one exact ComfyUI version.

RunPod flow for v2:

```bash
test -n "${HF_TOKEN:-}" || { echo "HF_TOKEN fehlt"; exit 1; }
curl -fsSL https://raw.githubusercontent.com/xxHalloxx/ComfyUI-MeetMap-UGC/main/bootstrap_runpod.sh \
  | env -u PIP_CONSTRAINT HF_TOKEN="${HF_TOKEN}" bash
```

The bootstrap copies committed references into `ComfyUI/input/meetmap_refs/`, downloads all v2 models idempotently, verifies every file, and installs `workflows/meetmap_ugc_v2.json`. Restart ComfyUI before importing it.

The v1 workflow remains available as `workflows/meetmap_ugc_realistic_runpod.json`.

During v2 execution, reference previews, processed-reference previews, raw/refined/final startframe previews and debug SaveImage outputs are visible before the LTX video finishes. FaceDetailer is present as an optional bypassed refinement stage (default OFF). No external inference API, Motion Control, SCAIL or source-video cloning is used.
