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

During v2 execution, active-reference previews, the raw FLUX startframe, YOLO face mask, face crop before/after FLUX.2 refinement, stitched face-realism frame, final LTX startframe, and debug outputs are visible before the LTX video finishes. FaceDetailer/Juggernaut is no longer used in the production path; refinement reuses FLUX.2 Klein through Crop & Stitch. No external inference API, Motion Control, SCAIL or source-video cloning is used.


## Dynamic creator references

The v2 workflow now reads creator references from:

```
ComfyUI/input/meetmap_refs/creator_01/
```

Supported files are `.png`, `.jpg`, `.jpeg`, and `.webp`. The default workflow uses up to 12 references, but the node is configurable up to 64. If fewer files exist, every available file is used. If more files exist than `max_references`, the loader uses the highest-priority files first and logs the final count.

Priority is read from `profile.json` when present. Face/identity references should come first, then skin texture, then body/outfit references. Add more same-creator images to `refs/creators/creator_01/`; no workflow JSON edit is required.

The separate `refs/style/` folder is not automatically mixed into identity conditioning. This avoids accidental identity drift if style references later contain other people.

The workflow shows a batch preview titled `PREVIEW – ACTIVE CREATOR REFERENCES` before FLUX sampling starts.


## Face realism pipeline

The v2 production workflow now uses a Reddit/community-style local face refinement path before LTX:

```
FLUX.2 startframe
  -> YOLO face detection (Impact Pack / Impact Subpack)
  -> Inpaint Crop at 1024px
  -> official FLUX.2 Klein 4B Image Edit subgraph
  -> Inpaint Stitch
  -> final 736x1312 LTX startframe
  -> LTX 2.5
```

The refinement prompt is deliberately identity-preserving and targets pores, skin microtexture, peach fuzz, eyelids, irises, eyelashes, eyebrows, lips, subtle asymmetry, smartphone softness, and sensor imperfections while explicitly avoiding beauty-filter/plastic-skin changes.

A separate eye-only pass is intentionally not enabled by default because independently refining the eyes can create mismatched eyes or identity drift. The face is refined as one coherent crop.

The workflow includes previews for the raw startframe, detected face mask, face crop before refinement, face crop after FLUX.2 refinement, stitched full frame, final LTX startframe, active creator references, and final video.

## App Mode

`meetmap_ugc_v2.json` contains ComfyUI App Mode metadata. It exposes the main generation controls plus advanced face-refinement controls, and surfaces generated topic/hook/script/prompts/acting beats together with all important intermediate images and the final video.


## Multi-video batches

The v2 workflow includes `MeetMapVideoBatchPlanner`. Set **Video count** in App Mode from 1 to 10.

The planner emits native ComfyUI list outputs, so downstream nodes are mapped once per requested video. This means each item gets a new MeetMap content generation, independent content/FLUX/face/LTX seeds, a new FLUX.2 start frame, the face-realism Crop & Stitch pass, and a separate LTX 2.5 render.

Batch controls:
- `video_count`: 1-10, default 1.
- `base_seed`: base value for deterministic batches.
- `seed_mode`: `increment` or deterministic `random_per_video`.
- `save_run_folder`: when enabled, outputs are grouped under a unique run folder.
- `filename_root`: default `video/meetmap_ugc_v2`.

The default `video_count = 1` preserves the normal single-video workflow.

## LTX 2.5 Motion Control

A second, separate workflow is available as:

`workflows/meetmap_ltx25_motion_control.json`

It is based on Lightricks' official LTX-2.5 Union Control workflow, adapted for **pose-motion transfer**:

```text
reference motion video
  -> DWPose
  -> pose control frames
  -> LTX Union Control IC-LoRA
  -> optional start-image conditioning
  -> LTX 2.5 generation
  -> decode
  -> saved video
```

The reference video supplies the motion/pose sequence. The optional start image and prompt define the generated subject and appearance. Depth and Canny branches were removed from this MeetMap variant so the workflow has one unambiguous motion-control path.

Install the additional motion-control dependencies and official BF16 model set with:

```bash
test -n "${HF_TOKEN:-}" || { echo "HF_TOKEN fehlt"; exit 1; }; \
curl -fsSL https://raw.githubusercontent.com/xxHalloxx/ComfyUI-MeetMap-UGC/main/install_ltx25_motion_control.sh \
  | env -u PIP_CONSTRAINT HF_TOKEN="${HF_TOKEN}" bash
```

This installs the pinned Lightricks ComfyUI-LTXVideo nodes, DWPose via `comfyui_controlnet_aux`, the official LTX-2.5 BF16 transformer/text encoders/VAEs/upscaler, and the Union Control IC-LoRA. Restart ComfyUI after installation.

### Motion Control V2

The improved workflow is:

`workflows/meetmap_ltx25_motion_control_v2.json`

V2 keeps the pose-only Union Control architecture and adds:

- a direct **motion strength** control wired into `LTXAddVideoICLoRAGuide` (default `0.90`);
- a **start-image / identity preview**;
- an **extracted DWPose motion preview** before the expensive LTX render;
- a stability-focused default negative prompt for identity drift, temporal flicker, anatomy warping and background morphing;
- clearer stage labels for motion generation vs identity/detail refinement;
- App Mode metadata exposing motion strength, image-input toggle, seeds and guidance controls.

Suggested motion strength range:
- `0.75–0.85`: more freedom / stronger appearance preservation;
- `0.90`: recommended balance;
- `0.95–1.00`: closest pose/movement adherence.

The installer copies both V1 and V2, with V2 as the recommended workflow after restart.

