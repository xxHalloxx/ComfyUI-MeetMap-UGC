# ComfyUI MeetMap UGC

Local custom nodes and RunPod bootstrap for MeetMap UGC v1/v2:

- `MeetMapContentGenerator` performs one CPU-only Qwen GGUF inference. If the documented default Qwen file is missing, it downloads the public file into `ComfyUI/models/LLM/` using normal Hugging Face environment authentication when available.
- `MeetMapLTXPromptBuilder` deterministically combines the video prompt with the exact German dialogue for LTX-2.5.
- `MeetMapLTXRelayPromptBuilder` deterministically turns Qwen acting beats into a time-segmented LTX relay prompt.
- `MeetMapReferenceFolderLoader` dynamically discovers creator reference images from a ComfyUI input folder, so the workflow is no longer limited to fixed reference slots.
- `MeetMapMultiReferenceConditioning` VAE-encodes the selected images one-by-one and appends them using ComfyUI's native `reference_latents` conditioning semantics for FLUX.2.

Designed for recent ComfyUI versions and standard RunPod ComfyUI images. The installer uses capability detection rather than enforcing one exact ComfyUI version.

RunPod flow for v2:

```bash
test -n "${HF_TOKEN:-}" || { echo "HF_TOKEN fehlt"; exit 1; }
curl -fsSL https://raw.githubusercontent.com/xxHalloxx/ComfyUI-MeetMap-UGC/main/bootstrap_runpod.sh \
  | env -u PIP_CONSTRAINT HF_TOKEN="${HF_TOKEN}" bash
```

The bootstrap recursively copies committed creator references into `ComfyUI/input/meetmap_refs/`, downloads all v2 models idempotently, verifies the creator pool plus every required model, and installs `workflows/meetmap_ugc_v2.json`. Restart ComfyUI before importing it.

The v1 workflow remains available as `workflows/meetmap_ugc_realistic_runpod.json`.

During v2 execution, reference previews, processed-reference previews, raw/refined/final startframe previews and debug SaveImage outputs are visible before the LTX video finishes. FaceDetailer is present as an optional bypassed refinement stage (default OFF). No external inference API, Motion Control, SCAIL or source-video cloning is used.


## Dynamic creator references

The v2 workflow now reads creator references from:

```
ComfyUI/input/meetmap_refs/creator_01/
```

Supported files are `.png`, `.jpg`, `.jpeg`, and `.webp`. The default workflow uses up to 12 references, but the node is configurable up to 64. If fewer files exist, every available file is used. If more files exist than `max_references`, the loader uses the highest-priority files first and logs the final count.

Priority is read from `profile.json` when present. Face/identity references should come first, then skin texture, then body/outfit references. Add more same-creator images to `refs/creators/creator_01/`; no workflow JSON edit is required.

The separate `refs/style/` folder is not automatically mixed into identity conditioning. This avoids accidental identity drift if style references later contain other people.

The workflow shows a batch preview titled `PREVIEW – ACTIVE CREATOR REFERENCES` before FLUX sampling starts.
