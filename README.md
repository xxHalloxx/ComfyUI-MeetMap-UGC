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

## SCAIL-2 Character Swap V2

The integrated character-replacement workflow is:

`workflows/meetmap_scail2_character_swap_v2.json`

It uses the current ComfyUI **SCAIL-2 Int8 Base** replacement graph as its generation core and adds a MeetMap wrapper for exact cropping, safe frame planning, previews, automatic stitch-back, and original-audio preservation.

Pipeline:

```text
full source video
  -> exact crop planner (32px aligned, max 81 frames)
  -> cropped driving video
  -> SCAIL-2 replacement (SAM3 masks + Int8 model + DPO + LightX2V)
  -> feathered crop composite back into untouched full frames
  -> original source audio trimmed to generated duration
  -> final full-frame video
```

The Base workflow is deliberately capped at 81 frames. Longer clips should use the SCAIL-2 Extend/chunked architecture rather than increasing the Base graph.

Install its models/runtime support with:

```bash
curl -fsSL https://raw.githubusercontent.com/xxHalloxx/ComfyUI-MeetMap-UGC/main/install_scail2_character_swap.sh \
  | env -u PIP_CONSTRAINT HF_TOKEN="${HF_TOKEN:-}" bash
```

Restart ComfyUI / the RunPod Pod after installation.

## Automated Google Drive → SCAIL-2 Character Swap V3

Recommended workflow:

`workflows/meetmap_scail2_character_swap_v3_drive.json`

This version is built around the intended production flow:

```text
Google Drive / MeetMap TikTok Content / Queue
  -> newest unprocessed video only
  -> claim/lease source file
  -> decode frames + original audio
  -> exact person crop
  -> extract first crop frame
  -> combine first frame + creator identity references
  -> FLUX.2 Klein regenerates frame 1 with the replacement character
  -> resize generated frame to exact SCAIL crop size
  -> SCAIL-2 replacement uses that generated frame as reference
  -> feathered stitch back into untouched original frames
  -> restore original source audio
  -> SaveVideo
  -> mark Drive source processed / optionally move it to processed folder
```

### Critical first-frame rule

V3 deliberately does **not** use the raw first frame as the SCAIL reference image. The exact first cropped frame is first edited by FLUX.2 Klein using the creator reference set. The edit prompt preserves the source pose, camera, scene, nearby objects and lighting while replacing the person with the creator identity. That generated frame then becomes the SCAIL-2 reference image.

### Google Drive queue semantics

`MeetMapGoogleDriveLatestVideo`:
- checks the configured Drive folder at every workflow run;
- verifies that the folder is named `Queue` and its direct parent is named `MeetMap TikTok Content`;
- selects the newest video that is not marked `meetmap_processed=true`;
- skips files with an active claim from another render;
- claims the chosen file for a configurable lease period (default 180 minutes);
- downloads it atomically into `ComfyUI/input/meetmap_drive/`;
- returns a native ComfyUI `VIDEO`, Drive file id and claim token.

`MeetMapGoogleDriveMarkProcessed` runs only after the final `SaveVideo` dependency succeeds. It verifies the claim token, marks the Drive file processed, clears the claim, and can optionally move the source video into a processed folder.

If a render fails before finalization, the claim expires and the source video becomes eligible again after the lease timeout.

### RunPod environment

Do not place Google credentials inside the workflow JSON or GitHub repo.

Set these as RunPod environment variables / secrets:

```text
GOOGLE_SERVICE_ACCOUNT_JSON=<full service account JSON>
MEETMAP_MOTION_DRIVE_FOLDER_ID=<folder id of MeetMap TikTok Content/Queue>
MEETMAP_MOTION_EXPECTED_FOLDER_NAME=Queue
MEETMAP_MOTION_EXPECTED_PARENT_FOLDER_NAME=MeetMap TikTok Content
MEETMAP_MOTION_PROCESSED_FOLDER_ID=<optional processed folder id>
```

Alternatively set `GOOGLE_SERVICE_ACCOUNT_FILE` to a mounted credential JSON path.

Share the parent folder **MeetMap TikTok Content** with the service-account email as **Editor**, so the runtime can verify its direct child **Queue**. The V3 loader fails closed if the configured folder is not `Queue` directly under `MeetMap TikTok Content`. Editor access is required for claim/processed metadata.

A safe variable-name template is included at:

`runpod_scail_v3.env.example`

### Creator references

The installer copies repo-managed references from:

`refs/creators/creator_01/`

to:

`ComfyUI/input/meetmap_refs/creator_01/`

The V3 workflow loads this folder automatically with `MeetMapReferenceFolderLoader`.

### Install

```bash
curl -fsSL https://raw.githubusercontent.com/xxHalloxx/ComfyUI-MeetMap-UGC/main/install_scail2_character_swap.sh \
  | env -u PIP_CONSTRAINT HF_TOKEN="${HF_TOKEN:-}" bash
```

The installer now ensures:
- SCAIL-2 Int8 ConvRot;
- SCAIL-2 DPO LoRA;
- LightX2V distilled LoRA;
- SAM3.1;
- Wan VAE / UMT5 / CLIP Vision;
- FLUX.2 Klein 4B FP8;
- FLUX.2 Qwen text encoder and VAE;
- Google Drive Python dependencies;
- creator reference files;
- V2 and V3 workflow JSON files.

Restart the Pod / ComfyUI process after installation.

### Safety limits

The Base V3 graph remains capped at 81 frames. For longer clips, use a future SCAIL-2 Extend/chunked variant instead of raising the Base limit. The Drive loader also has a configurable maximum source file size and sanitizes all downloaded filenames/paths.

