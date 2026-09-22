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
        "Collects mapped SCAIL chunk outputs, drops the repeated overlap from every "
        "segment after the first, concatenates them in order, and trims to the exact target."
    )

    def stitch(self, chunks, overlap_frames, target_frame_count):
        tensors = _flatten_tensors(chunks)
        if not tensors:
            raise RuntimeError("No SCAIL chunks were provided for stitching.")

        overlap = max(0, int(_first_scalar(overlap_frames, 5)))
        target = max(1, int(_first_scalar(target_frame_count, 1)))

        prepared = []
        expected_shape = None
        source_lengths = []
        for idx, tensor in enumerate(tensors):
            if tensor.ndim != 4:
                raise ValueError(
                    f"SCAIL chunk #{idx + 1} must be IMAGE [B,H,W,C], got {tuple(tensor.shape)}."
                )
            if tensor.shape[0] < 1:
                raise ValueError(f"SCAIL chunk #{idx + 1} is empty.")

            shape = tuple(tensor.shape[1:])
            if expected_shape is None:
                expected_shape = shape
            elif shape != expected_shape:
                raise ValueError(
                    f"SCAIL chunk resolution mismatch: expected {expected_shape}, got {shape}."
                )

            source_lengths.append(int(tensor.shape[0]))
            if idx == 0:
                prepared.append(tensor)
            else:
                if tensor.shape[0] <= overlap:
                    raise ValueError(
                        f"SCAIL chunk #{idx + 1} has only {tensor.shape[0]} frames, "
                        f"not enough for {overlap}-frame overlap."
                    )
                prepared.append(tensor[overlap:])

        result = torch.cat(prepared, dim=0)
        if result.shape[0] < target:
            raise RuntimeError(
                f"Stitched SCAIL result is too short: {result.shape[0]} < target {target}."
            )
        result = result[:target]

        status = (
            f"Stitched {len(tensors)} SCAIL segment(s) {source_lengths} with "
            f"{overlap}-frame overlap -> {int(result.shape[0])} final frames."
        )
        return (result, status)


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
        "Execution barrier between visual generation and Seed-VC. It waits for the final "
        "visual frames, unloads ComfyUI models, runs GC/cache cleanup, then passes audio."
    )

    def release(self, audio, visual_dependency):
        # The dependency is intentionally unused as data; its presence forces the complete
        # visual branch to finish before we unload SCAIL/FLUX and start Seed-VC.
        if visual_dependency is None:
            raise RuntimeError("Visual dependency is missing; refusing early audio execution.")

        try:
            import comfy.model_management as model_management

            model_management.unload_all_models()
            gc.collect()
            model_management.soft_empty_cache()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            status = "Visual models unloaded and GPU cache released before Seed-VC."
        except Exception as exc:
            # Fail closed: if cleanup itself is broken we do not continue into another
            # heavyweight model and risk an avoidable OOM/costly failed job.
            raise RuntimeError(f"VRAM cleanup before Seed-VC failed: {exc}") from exc

        return (audio, status)


NODE_CLASS_MAPPINGS = {
    "MeetMapSCAILLongVideoPlanner": MeetMapSCAILLongVideoPlanner,
    "MeetMapSCAILChunkStitch": MeetMapSCAILChunkStitch,
    "MeetMapReleaseVRAMThenPassAudio": MeetMapReleaseVRAMThenPassAudio,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeetMapSCAILLongVideoPlanner": "MeetMap SCAIL Long Video Planner",
    "MeetMapSCAILChunkStitch": "MeetMap SCAIL Chunk Stitch",
    "MeetMapReleaseVRAMThenPassAudio": "MeetMap Release VRAM Then Pass Audio",
}
