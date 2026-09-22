import json
import math
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageOps

import folder_paths
import node_helpers


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _input_root() -> Path:
    return Path(folder_paths.get_input_directory()).resolve()


def _resolve_reference_folder(reference_folder: str) -> Path:
    root = _input_root()
    value = str(reference_folder).strip().replace("\\", "/").lstrip("/")
    folder = (root / value).resolve()
    try:
        folder.relative_to(root)
    except ValueError as exc:
        raise ValueError("reference_folder must stay inside ComfyUI/input.") from exc
    if not folder.is_dir():
        raise FileNotFoundError(f"Reference folder does not exist: {folder}")
    return folder


def _load_priority_names(folder: Path):
    profile = folder / "profile.json"
    if not profile.is_file():
        return []
    try:
        data = json.loads(profile.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    ordered = []
    for key in ("identity_priority", "texture_priority", "body_priority", "identity_refs"):
        value = data.get(key, [])
        if isinstance(value, list):
            for item in value:
                name = Path(str(item)).name
                if name and name not in ordered:
                    ordered.append(name)
    return ordered


def _discover_files(folder: Path, include_subfolders: bool, selection_mode: str):
    iterator = folder.rglob("*") if include_subfolders else folder.iterdir()
    files = [
        path
        for path in iterator
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS and path.stat().st_size > 0
    ]
    files.sort(key=lambda path: path.relative_to(folder).as_posix().lower())

    if selection_mode == "profile_priority":
        priority = _load_priority_names(folder)
        if priority:
            rank = {name.lower(): index for index, name in enumerate(priority)}
            files.sort(
                key=lambda path: (
                    rank.get(path.name.lower(), len(rank)),
                    path.relative_to(folder).as_posix().lower(),
                )
            )
    return files


def _pil_to_tensor(image: Image.Image):
    array = np.array(image.convert("RGB"), dtype=np.float32, copy=True) / 255.0
    return torch.from_numpy(array).unsqueeze(0)


def _preview_tensor(image: Image.Image, size: int = 384):
    rgb = image.convert("RGB")
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    contained = ImageOps.contain(rgb, (size, size), method=resampling)
    canvas = Image.new("RGB", (size, size), (24, 24, 24))
    x = (size - contained.width) // 2
    y = (size - contained.height) // 2
    canvas.paste(contained, (x, y))
    return _pil_to_tensor(canvas)


def _scale_to_megapixels(image: torch.Tensor, megapixels: float):
    if image.ndim != 4 or image.shape[-1] < 3:
        raise ValueError("Reference image tensor must be NHWC IMAGE data.")
    height = int(image.shape[1])
    width = int(image.shape[2])
    if height <= 0 or width <= 0:
        raise ValueError("Reference image has invalid dimensions.")

    target_pixels = max(0.05, float(megapixels)) * 1_000_000.0
    scale = math.sqrt(target_pixels / float(height * width))
    new_height = max(64, int(round((height * scale) / 8.0)) * 8)
    new_width = max(64, int(round((width * scale) / 8.0)) * 8)

    chw = image[..., :3].movedim(-1, 1)
    resized = F.interpolate(
        chw,
        size=(new_height, new_width),
        mode="bicubic",
        align_corners=False,
        antialias=True,
    )
    return resized.movedim(1, -1).clamp_(0.0, 1.0)



def _resize_cover(image: torch.Tensor, target_height: int, target_width: int):
    if image.ndim != 4 or image.shape[0] < 1 or image.shape[-1] < 3:
        raise ValueError("IMAGE data must be NHWC.")
    image = image[:1, ..., :3]
    h = int(image.shape[1])
    w = int(image.shape[2])
    if h <= 0 or w <= 0:
        raise ValueError("Reference image has invalid dimensions.")

    scale = max(float(target_height) / float(h), float(target_width) / float(w))
    resized_h = max(target_height, int(math.ceil(h * scale)))
    resized_w = max(target_width, int(math.ceil(w * scale)))

    chw = image.movedim(-1, 1)
    resized = F.interpolate(
        chw,
        size=(resized_h, resized_w),
        mode="bicubic",
        align_corners=False,
        antialias=True,
    )
    top = max(0, (resized_h - target_height) // 2)
    left = max(0, (resized_w - target_width) // 2)
    cropped = resized[:, :, top:top + target_height, left:left + target_width]
    return cropped.movedim(1, -1).clamp_(0.0, 1.0)


class MeetMapSceneReferenceBatch:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "scene_image": ("IMAGE",),
                "references": ("MEETMAP_REFERENCE_SET",),
                "max_references": ("INT", {"default": 6, "min": 1, "max": 12}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image_batch", "status")
    FUNCTION = "build"
    CATEGORY = "MeetMap/UGC"
    DESCRIPTION = (
        "Builds a FLUX.2 image-edit reference batch where frame 1 is the exact source scene/pose "
        "and later frames are creator identity references resized with center-crop to the same size."
    )

    def build(self, scene_image, references, max_references):
        if scene_image is None or scene_image.ndim != 4 or scene_image.shape[0] < 1:
            raise ValueError("scene_image must contain at least one IMAGE frame.")
        if not isinstance(references, (list, tuple)):
            references = []

        scene = scene_image[:1, ..., :3].clamp(0.0, 1.0)
        target_h = int(scene.shape[1])
        target_w = int(scene.shape[2])

        selected = list(references)[: max(1, int(max_references))]
        prepared = [scene]
        names = []
        warnings = []
        for index, item in enumerate(selected, start=1):
            image = item.get("image") if isinstance(item, dict) else None
            if image is None:
                warnings.append(f"reference #{index} missing IMAGE data")
                continue
            try:
                prepared.append(_resize_cover(image, target_h, target_w))
            except Exception as exc:
                warnings.append(f"reference #{index} skipped: {exc}")
                continue
            names.append(
                str(item.get("filename", f"reference_{index:02d}"))
                if isinstance(item, dict)
                else f"reference_{index:02d}"
            )

        batch = torch.cat(prepared, dim=0)
        status = (
            f"FLUX scene/reference batch: 1 source-scene frame + {len(names)} identity refs "
            f"at {target_w}x{target_h}."
        )
        if names:
            status += " Identity refs: " + ", ".join(names) + "."
        else:
            status += " WARNING: no usable creator refs; continuing with source scene only."
        if warnings:
            status += " Recovery: " + "; ".join(warnings) + "."
        print("[MeetMap UGC] " + status)
        return (batch, status)


class MeetMapSCAILReferenceBatch:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "primary_image": ("IMAGE",),
                "references": ("MEETMAP_REFERENCE_SET",),
                "priority_filenames": (
                    "STRING",
                    {
                        "default": "face_front.png\nface_angle.png\nupper_body.png",
                        "multiline": True,
                    },
                ),
                "max_additional_references": (
                    "INT",
                    {"default": 3, "min": 1, "max": 6},
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image_batch", "status")
    FUNCTION = "build"
    CATEGORY = "MeetMap/SCAIL"
    DESCRIPTION = (
        "Builds the native SCAIL-2 multi-reference image batch. The generated character "
        "start frame stays first/primary; selected creator views follow as additional refs."
    )

    def build(self, primary_image, references, priority_filenames, max_additional_references):
        if primary_image is None or primary_image.ndim != 4 or primary_image.shape[0] < 1:
            raise ValueError("primary_image must contain at least one IMAGE frame.")
        if not isinstance(references, (list, tuple)):
            references = []

        primary = primary_image[:1, ..., :3].clamp(0.0, 1.0)
        target_h = int(primary.shape[1])
        target_w = int(primary.shape[2])
        limit = max(1, min(6, int(max_additional_references)))

        by_name = {}
        ordered = []
        for item in references:
            if not isinstance(item, dict) or item.get("image") is None:
                continue
            name = str(item.get("filename", "")).replace("\\", "/")
            by_name[name] = item
            by_name[Path(name).name] = item
            ordered.append(item)

        requested = [
            line.strip().replace("\\", "/")
            for line in str(priority_filenames or "").splitlines()
            if line.strip()
        ]

        selected = []
        seen = set()
        for wanted in requested:
            item = by_name.get(wanted) or by_name.get(Path(wanted).name)
            if item is None:
                continue
            key = str(item.get("filename", wanted))
            if key in seen:
                continue
            selected.append(item)
            seen.add(key)
            if len(selected) >= limit:
                break

        # Fail-soft on naming only: if one requested view was renamed, fill the remaining
        # slots from the already validated reference set instead of silently losing
        # multi-reference conditioning.
        if len(selected) < limit:
            for item in ordered:
                key = str(item.get("filename", ""))
                if key in seen:
                    continue
                selected.append(item)
                seen.add(key)
                if len(selected) >= limit:
                    break

        batch_parts = [primary]
        names = []
        warnings = []
        for index, item in enumerate(selected, start=1):
            image = item.get("image") if isinstance(item, dict) else None
            if image is None:
                warnings.append(f"reference #{index} missing IMAGE data")
                continue
            try:
                batch_parts.append(_resize_cover(image, target_h, target_w))
            except Exception as exc:
                warnings.append(f"reference #{index} skipped: {exc}")
                continue
            names.append(str(item.get("filename", f"reference_{index:02d}")))

        batch = torch.cat(batch_parts, dim=0)
        status = (
            f"SCAIL-2 multi-reference batch: 1 generated primary + {len(names)} additional "
            f"creator views at {target_w}x{target_h}."
        )
        if names:
            status += " Refs: " + ", ".join(names) + "."
        else:
            status += " WARNING: no usable creator refs; continuing with generated primary only."
        if warnings:
            status += " Recovery: " + "; ".join(warnings) + "."
        print("[MeetMap SCAIL] " + status)
        return (batch, status)


class MeetMapReferenceFolderLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "reference_folder": ("STRING", {"default": "meetmap_refs/creator_01"}),
                "max_references": ("INT", {"default": 12, "min": 1, "max": 64}),
                "selection_mode": (["profile_priority", "alphabetical"], {"default": "profile_priority"}),
                "include_subfolders": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = ("MEETMAP_REFERENCE_SET", "IMAGE", "INT", "STRING")
    RETURN_NAMES = ("references", "preview_images", "reference_count", "filenames")
    FUNCTION = "load"
    CATEGORY = "MeetMap/UGC"
    DESCRIPTION = "Loads a dynamic set of creator reference images from a ComfyUI input subfolder."

    @classmethod
    def IS_CHANGED(cls, reference_folder, max_references, selection_mode, include_subfolders):
        try:
            folder = _resolve_reference_folder(reference_folder)
            files = _discover_files(folder, bool(include_subfolders), str(selection_mode))
            return tuple(
                (
                    path.relative_to(folder).as_posix(),
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                )
                for path in files[: int(max_references)]
            )
        except Exception:
            return float("nan")

    def load(self, reference_folder, max_references, selection_mode, include_subfolders):
        try:
            folder = _resolve_reference_folder(reference_folder)
            files = _discover_files(folder, bool(include_subfolders), str(selection_mode))
        except Exception as exc:
            placeholder = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
            warning = f"WARNING: creator reference folder unavailable ({exc}); continuing with zero creator refs."
            print("[MeetMap UGC] " + warning)
            return ([], placeholder, 0, warning)

        if not files:
            placeholder = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
            warning = f"WARNING: no creator reference images found in {folder}; continuing with zero creator refs."
            print("[MeetMap UGC] " + warning)
            return ([], placeholder, 0, warning)

        limit = max(1, int(max_references))
        selected = files[:limit]
        if len(files) > limit:
            print(f"[MeetMap UGC] Using {len(selected)} of {len(files)} available references from {folder}.")
        else:
            print(f"[MeetMap UGC] Using all {len(selected)} references from {folder}.")

        references = []
        previews = []
        for path in selected:
            try:
                with Image.open(path) as opened:
                    image = ImageOps.exif_transpose(opened).convert("RGB")
                    tensor = _pil_to_tensor(image)
                    preview = _preview_tensor(image)
            except Exception as exc:
                print(f"[MeetMap UGC] Skipping unreadable reference {path}: {exc}")
                continue
            references.append(
                {
                    "image": tensor,
                    "filename": path.relative_to(folder).as_posix(),
                }
            )
            previews.append(preview)

        if not references:
            placeholder = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
            warning = f"WARNING: all creator reference images in {folder} were unreadable; continuing with zero creator refs."
            print("[MeetMap UGC] " + warning)
            return ([], placeholder, 0, warning)

        preview_batch = torch.cat(previews, dim=0)
        filenames = "\n".join(item["filename"] for item in references)
        return (references, preview_batch, len(references), filenames)


class MeetMapMultiReferenceConditioning:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive": ("CONDITIONING",),
                "negative": ("CONDITIONING",),
                "references": ("MEETMAP_REFERENCE_SET",),
                "vae": ("VAE",),
                "max_references": ("INT", {"default": 12, "min": 1, "max": 64}),
                "reference_megapixels": (
                    "FLOAT",
                    {"default": 1.0, "min": 0.1, "max": 2.0, "step": 0.05},
                ),
            }
        }

    RETURN_TYPES = ("CONDITIONING", "CONDITIONING", "INT", "STRING")
    RETURN_NAMES = ("positive", "negative", "used_count", "used_filenames")
    FUNCTION = "apply"
    CATEGORY = "MeetMap/UGC"
    DESCRIPTION = "VAE-encodes a dynamic creator reference set and appends every latent using ComfyUI's native reference_latents semantics."

    def apply(self, positive, negative, references, vae, max_references, reference_megapixels):
        if not isinstance(references, (list, tuple)):
            references = []

        selected = list(references)[: max(1, int(max_references))]
        used_names = []
        warnings = []

        for index, item in enumerate(selected, start=1):
            image = item.get("image") if isinstance(item, dict) else None
            if image is None:
                warnings.append(f"reference #{index} missing IMAGE data")
                continue

            try:
                prepared = _scale_to_megapixels(image, float(reference_megapixels))
                samples = vae.encode(prepared[..., :3])
                values = {"reference_latents": [samples]}
                positive = node_helpers.conditioning_set_values(positive, values, append=True)
                negative = node_helpers.conditioning_set_values(negative, values, append=True)
            except Exception as exc:
                warnings.append(f"reference #{index} conditioning skipped: {exc}")
                continue

            filename = item.get("filename", f"reference_{index:02d}") if isinstance(item, dict) else f"reference_{index:02d}"
            used_names.append(str(filename))

        status = f"Applied {len(used_names)} FLUX reference latent(s)."
        if not used_names:
            status += " WARNING: no usable reference latents; base conditioning passed through unchanged."
        if warnings:
            status += " Recovery: " + "; ".join(warnings) + "."
        print("[MeetMap UGC] " + status)
        names = "\n".join(used_names)
        if warnings:
            names += ("\n" if names else "") + "WARNING: " + "; ".join(warnings)
        return (positive, negative, len(used_names), names)


NODE_CLASS_MAPPINGS = {
    "MeetMapSceneReferenceBatch": MeetMapSceneReferenceBatch,
    "MeetMapSCAILReferenceBatch": MeetMapSCAILReferenceBatch,
    "MeetMapReferenceFolderLoader": MeetMapReferenceFolderLoader,
    "MeetMapMultiReferenceConditioning": MeetMapMultiReferenceConditioning,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeetMapSceneReferenceBatch": "MeetMap Scene + Character Reference Batch",
    "MeetMapSCAILReferenceBatch": "MeetMap SCAIL-2 Multi Reference Batch",
    "MeetMapReferenceFolderLoader": "MeetMap Reference Folder Loader",
    "MeetMapMultiReferenceConditioning": "MeetMap Multi Reference Conditioning",
}
