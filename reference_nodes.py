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
        folder = _resolve_reference_folder(reference_folder)
        files = _discover_files(folder, bool(include_subfolders), str(selection_mode))
        if not files:
            raise RuntimeError(f"No creator reference images found in: {folder}")

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
            raise RuntimeError(f"No readable creator reference images found in: {folder}")

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
        if not isinstance(references, (list, tuple)) or not references:
            raise RuntimeError("MeetMap reference set is empty.")

        selected = list(references)[: max(1, int(max_references))]
        used_names = []

        for index, item in enumerate(selected, start=1):
            image = item.get("image") if isinstance(item, dict) else None
            if image is None:
                raise ValueError(f"Reference #{index} is missing IMAGE data.")

            prepared = _scale_to_megapixels(image, float(reference_megapixels))
            samples = vae.encode(prepared[..., :3])

            values = {"reference_latents": [samples]}
            positive = node_helpers.conditioning_set_values(positive, values, append=True)
            negative = node_helpers.conditioning_set_values(negative, values, append=True)

            filename = item.get("filename", f"reference_{index:02d}") if isinstance(item, dict) else f"reference_{index:02d}"
            used_names.append(str(filename))

        print(f"[MeetMap UGC] Applied {len(used_names)} FLUX reference latents.")
        return (positive, negative, len(used_names), "\n".join(used_names))


NODE_CLASS_MAPPINGS = {
    "MeetMapReferenceFolderLoader": MeetMapReferenceFolderLoader,
    "MeetMapMultiReferenceConditioning": MeetMapMultiReferenceConditioning,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeetMapReferenceFolderLoader": "MeetMap Reference Folder Loader",
    "MeetMapMultiReferenceConditioning": "MeetMap Multi Reference Conditioning",
}
