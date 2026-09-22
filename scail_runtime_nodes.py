import gc
import math

import torch


def _first_scalar(value, default=0):
    if isinstance(value, (list, tuple)):
        if not value:
            return default
        return _first_scalar(value[0], default)
    return value


def _flatten_tensors(value):
    if isinstance(value, torch.Tensor):
        return [value]
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_flatten_tensors(item))
        return out
    return []


class MeetMapSCAILLongVideoPlanner:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "total_frames": ("INT", {"forceInput": True, "min": 1, "max": 100000}),
                "chunk_size": ("INT", {"default": 81, "min": 5, "max": 81, "step": 4}),
                "overlap_frames": ("INT", {"default": 5, "min": 1, "max": 21, "step": 4}),
                "max_segments": ("INT", {"default": 32, "min": 1, "max": 128}),
            }
        }

    RETURN_TYPES = ("INT", "INT", "INT", "STRING")
    RETURN_NAMES = ("start_frame", "chunk_length", "segment_index", "status")
    OUTPUT_IS_LIST = (True, True, True, False)
    FUNCTION = "plan"
    CATEGORY = "MeetMap/SCAIL"
    DESCRIPTION = (
        "Splits the requested SCAIL render into 4n+1 chunks. Default is the official "
        "81-frame chunk with 5-frame overlap (76-frame stride). Outputs list values so "
        "the downstream SCAIL subgraph is mapped once per required segment."
    )

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def plan(self, total_frames, chunk_size, overlap_frames, max_segments):
        total = max(1, int(total_frames))

        chunk = max(5, min(81, int(chunk_size)))
        chunk = ((chunk - 1) // 4) * 4 + 1

        overlap = max(1, int(overlap_frames))
        overlap = ((overlap - 1) // 4) * 4 + 1
        if overlap >= chunk:
            raise ValueError(
                f"SCAIL overlap_frames ({overlap}) must be smaller than chunk_size ({chunk})."
            )

        stride = chunk - overlap
        starts = []
        lengths = []
        indices = []

        start = 0
        index = 1
        while start < total:
            remaining = total - start
            length = min(chunk, remaining)

            # total_frames and every start are 4n+1 / multiples of 4 respectively,
            # therefore the remainder should already be 4n+1. Keep this fail-closed
            # check so a future upstream change cannot silently create invalid Wan latents.
            if length > 1 and (length - 1) % 4 != 0:
                length = ((length - 1) // 4) * 4 + 1
            if length <= 0:
                break

            starts.append(start)
            lengths.append(length)
            indices.append(index)

            if start + length >= total:
                break
            start += stride
            index += 1

            if len(starts) >= int(max_segments):
                raise RuntimeError(
                    f"Video requires more than max_segments={int(max_segments)}. "
                    "Refusing an unexpectedly expensive SCAIL job."
                )

        status = (
            f"SCAIL long-video plan: {total} frames -> {len(starts)} segment(s), "
            f"chunk={chunk}, overlap={overlap}, stride={stride}; starts={starts}; "
            f"lengths={lengths}."
        )
        return (starts, lengths, indices, status)


class MeetMapSCAILChunkStitch:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "chunks": ("IMAGE",),
                "overlap_frames": ("INT", {"default": 5, "min": 1, "max": 81}),
                "target_frame_count": ("INT", {"forceInput": True, "min": 1, "max": 100000}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "status")
    FUNCTION = "stitch"
    CATEGORY = "MeetMap/SCAIL"
    DESCRIPTION = (
        "Fault-tolerant SCAIL chunk stitcher. It repairs small resolution mismatches, "
        "tolerates short final chunks, removes overlap, and pads a short final result "
        "with the last valid frame instead of failing the whole workflow."
    )

    def stitch(self, chunks, overlap_frames, target_frame_count):
        tensors = _flatten_tensors(chunks)
        if not tensors:
            raise RuntimeError("No SCAIL chunks were produced; there is no valid visual fallback to stitch.")

        overlap = max(0, int(_first_scalar(overlap_frames, 5)))
        target = max(1, int(_first_scalar(target_frame_count, 1)))
        prepared = []
        source_lengths = []
        warnings = []
        target_h = target_w = target_c = None

        for idx, tensor in enumerate(tensors):
            if not isinstance(tensor, torch.Tensor) or tensor.ndim != 4 or tensor.shape[0] < 1:
                warnings.append(f"ignored invalid chunk #{idx + 1}")
                continue

            tensor = tensor[..., :3].clamp(0.0, 1.0)
            source_lengths.append(int(tensor.shape[0]))
            h, w, ch = map(int, tensor.shape[1:])

            if target_h is None:
                target_h, target_w, target_c = h, w, ch
            elif (h, w, ch) != (target_h, target_w, target_c):
                try:
                    import torch.nn.functional as F
                    tensor = tensor.permute(0, 3, 1, 2)
                    tensor = F.interpolate(
                        tensor,
                        size=(target_h, target_w),
                        mode="bilinear",
                        align_corners=False,
                    )
                    tensor = tensor.permute(0, 2, 3, 1).contiguous()
                    warnings.append(
                        f"resized chunk #{idx + 1} from {w}x{h} to {target_w}x{target_h}"
                    )
                except Exception as exc:
                    warnings.append(f"ignored mismatched chunk #{idx + 1}: {exc}")
                    continue

            if not prepared:
                prepared.append(tensor)
                continue

            drop = min(overlap, max(0, int(tensor.shape[0]) - 1))
            if drop < overlap:
                warnings.append(
                    f"chunk #{idx + 1} was shorter than expected; removed only {drop} overlap frame(s)"
                )
            prepared.append(tensor[drop:])

        if not prepared:
            raise RuntimeError("All SCAIL chunks were invalid; no usable frames remain.")

        result = torch.cat(prepared, dim=0)
        if result.shape[0] < target:
            missing = target - int(result.shape[0])
            last = result[-1:].repeat(missing, 1, 1, 1)
            result = torch.cat([result, last], dim=0)
            warnings.append(f"padded {missing} missing frame(s) with the last valid frame")
        elif result.shape[0] > target:
            result = result[:target]

        status = (
            f"Stitched {len(prepared)} usable SCAIL segment(s) {source_lengths} with "
            f"{overlap}-frame overlap -> {int(result.shape[0])} final frames."
        )
        if warnings:
            status += " Recovery: " + "; ".join(warnings) + "."
        return (result, status)


class MeetMapOptionalLoraModelLoader:
    def __init__(self):
        self.loaded_lora = None

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "preferred_lora": (
                    "STRING",
                    {"default": "", "multiline": False},
                ),
                "strength_model": (
                    "FLOAT",
                    {"default": 1.0, "min": -10.0, "max": 10.0, "step": 0.05},
                ),
                "enabled": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "fallback_loras": (
                    "STRING",
                    {"default": "", "multiline": True},
                ),
            },
        }

    RETURN_TYPES = ("MODEL", "STRING")
    RETURN_NAMES = ("model", "status")
    FUNCTION = "load"
    CATEGORY = "MeetMap/Runtime"
    DESCRIPTION = (
        "Loads a LoRA by string rather than a validating COMBO. If the preferred file "
        "is missing/corrupt/incompatible it tries fallback names and finally passes the "
        "original model through instead of invalidating the entire prompt."
    )

    @staticmethod
    def _candidate_names(preferred, fallback_loras, available):
        requested = []
        for value in [preferred, *(str(fallback_loras or "").replace(",", "\n").splitlines())]:
            value = str(value or "").strip().replace("\\", "/")
            if value and value not in requested:
                requested.append(value)

        exact = {name.lower(): name for name in available}
        by_base = {}
        for name in available:
            by_base.setdefault(name.rsplit("/", 1)[-1].lower(), name)

        resolved = []
        for value in requested:
            found = exact.get(value.lower()) or by_base.get(value.rsplit("/", 1)[-1].lower())
            if found and found not in resolved:
                resolved.append(found)
        return requested, resolved

    def load(self, model, preferred_lora, strength_model, enabled, fallback_loras=""):
        if not bool(enabled) or float(strength_model) == 0.0:
            status = "Optional LoRA disabled; base model passed through."
            print("[MeetMap SCAIL] " + status)
            return (model, status)

        try:
            import folder_paths
            import comfy.sd
            import comfy.utils
        except Exception as exc:
            status = f"WARNING: optional LoRA runtime unavailable; base model used: {exc}"
            print("[MeetMap SCAIL] " + status)
            return (model, status)

        available = list(folder_paths.get_filename_list("loras"))
        requested, candidates = self._candidate_names(preferred_lora, fallback_loras, available)
        if not candidates:
            status = (
                "WARNING: optional LoRA missing; base model used. Requested: "
                + (", ".join(requested) if requested else "<empty>")
            )
            print("[MeetMap SCAIL] " + status)
            return (model, status)

        failures = []
        for name in candidates:
            try:
                path = folder_paths.get_full_path_or_raise("loras", name)
                cache_key = (path, float(strength_model))
                if self.loaded_lora is not None and self.loaded_lora[0] == cache_key:
                    lora, metadata = self.loaded_lora[1], self.loaded_lora[2]
                else:
                    lora, metadata = comfy.utils.load_torch_file(
                        path,
                        safe_load=True,
                        return_metadata=True,
                    )
                    self.loaded_lora = (cache_key, lora, metadata)

                model_lora, _ = comfy.sd.load_lora_for_models(
                    model,
                    None,
                    lora,
                    float(strength_model),
                    0,
                    lora_metadata=metadata,
                )
                status = f"Applied optional LoRA '{name}' at strength {float(strength_model):.2f}."
                print("[MeetMap SCAIL] " + status)
                return (model_lora, status)
            except Exception as exc:
                failures.append(f"{name}: {exc}")

        status = "WARNING: all optional LoRA candidates failed; base model used. " + " | ".join(failures)
        print("[MeetMap SCAIL] " + status)
        return (model, status)


class MeetMapReleaseVRAMThenPassAudio:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO",),
                "visual_dependency": ("IMAGE",),
            }
        }

    RETURN_TYPES = ("AUDIO", "STRING")
    RETURN_NAMES = ("audio", "status")
    FUNCTION = "release"
    CATEGORY = "MeetMap/Runtime"
    DESCRIPTION = (
        "Execution barrier between visual generation and Seed-VC. Cleanup is best-effort "
        "with multiple fallbacks so a cache-cleanup API change does not kill a completed render."
    )

    def release(self, audio, visual_dependency):
        if visual_dependency is None:
            return (audio, "VRAM barrier warning: visual dependency missing; audio passed through.")

        notes = []
        try:
            import comfy.model_management as model_management
        except Exception as exc:
            model_management = None
            notes.append(f"model_management unavailable: {exc}")

        if model_management is not None:
            try:
                model_management.unload_all_models()
                notes.append("unload_all_models ok")
            except Exception as exc:
                notes.append(f"unload_all_models skipped: {exc}")

        try:
            gc.collect()
            notes.append("gc ok")
        except Exception as exc:
            notes.append(f"gc skipped: {exc}")

        if model_management is not None:
            try:
                model_management.soft_empty_cache()
                notes.append("soft_empty_cache ok")
            except Exception as exc:
                notes.append(f"soft_empty_cache skipped: {exc}")

        if torch.cuda.is_available():
            try:
                torch.cuda.empty_cache()
                notes.append("cuda empty_cache ok")
            except Exception as exc:
                notes.append(f"cuda empty_cache skipped: {exc}")

        return (audio, "VRAM cleanup barrier completed. " + "; ".join(notes))


class MeetMapStatusCollector:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "drive": ("STRING", {"forceInput": True}),
                "references": ("STRING", {"forceInput": True}),
                "chunk_plan": ("STRING", {"forceInput": True}),
                "stitch": ("STRING", {"forceInput": True}),
                "vram": ("STRING", {"forceInput": True}),
                "voice_reference": ("STRING", {"forceInput": True}),
                "voice_conversion": ("STRING", {"forceInput": True}),
                "save": ("STRING", {"forceInput": True}),
                "drive_finalize": ("STRING", {"forceInput": True}),
                "source_audio": ("STRING", {"forceInput": True}),
                "first_frame": ("STRING", {"forceInput": True}),
            }
        }

    RETURN_TYPES = ("STRING", "INT", "BOOLEAN")
    RETURN_NAMES = ("summary", "warning_count", "used_fallback")
    FUNCTION = "collect"
    OUTPUT_NODE = True
    CATEGORY = "MeetMap/Runtime"

    def collect(
        self,
        drive,
        references,
        chunk_plan,
        stitch,
        vram,
        voice_reference,
        voice_conversion,
        save,
        drive_finalize,
        source_audio,
        first_frame,
    ):
        items = [
            ("drive", drive),
            ("references", references),
            ("chunk_plan", chunk_plan),
            ("stitch", stitch),
            ("vram", vram),
            ("voice_reference", voice_reference),
            ("voice_conversion", voice_conversion),
            ("save", save),
            ("drive_finalize", drive_finalize),
            ("source_audio", source_audio),
            ("first_frame", first_frame),
        ]

        warning_terms = (
            "warning",
            "fallback",
            "failed",
            "missing",
            "unavailable",
            "skipped",
            "recovery",
            "original source audio used",
            "base model used",
            "zero creator refs",
        )
        warnings = []
        lines = []
        for name, value in items:
            text = str(value or "").strip()
            if not text:
                text = "no status text"
            lines.append(f"{name}: {text}")
            lowered = text.lower()
            if any(term in lowered for term in warning_terms):
                warnings.append(name)

        summary = (
            f"MeetMap run completed with {len(warnings)} warning/fallback stage(s). "
            + ("Fallback stages: " + ", ".join(warnings) + ". " if warnings else "No fallbacks reported. ")
            + "\n".join(lines)
        )
        print("[MeetMap Summary] " + summary.replace("\n", " | "))
        return (summary, len(warnings), bool(warnings))


NODE_CLASS_MAPPINGS = {
    "MeetMapSCAILLongVideoPlanner": MeetMapSCAILLongVideoPlanner,
    "MeetMapSCAILChunkStitch": MeetMapSCAILChunkStitch,
    "MeetMapOptionalLoraModelLoader": MeetMapOptionalLoraModelLoader,
    "MeetMapReleaseVRAMThenPassAudio": MeetMapReleaseVRAMThenPassAudio,
    "MeetMapStatusCollector": MeetMapStatusCollector,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeetMapSCAILLongVideoPlanner": "MeetMap SCAIL Long Video Planner",
    "MeetMapSCAILChunkStitch": "MeetMap SCAIL Chunk Stitch",
    "MeetMapOptionalLoraModelLoader": "MeetMap Optional LoRA Model Loader",
    "MeetMapReleaseVRAMThenPassAudio": "MeetMap Release VRAM Then Pass Audio",
    "MeetMapStatusCollector": "MeetMap Run Status Collector",
}
