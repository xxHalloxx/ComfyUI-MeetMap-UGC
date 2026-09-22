from pathlib import Path

import folder_paths
import torchaudio


class MeetMapCreatorVoiceReference:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "reference_path": (
                    "STRING",
                    {
                        "default": "meetmap_refs/creator_01/voice/reference.wav",
                        "multiline": False,
                    },
                ),
            }
        }

    RETURN_TYPES = ("AUDIO", "STRING")
    RETURN_NAMES = ("audio", "status")
    FUNCTION = "load"
    CATEGORY = "MeetMap/Voice"
    DESCRIPTION = (
        "Loads the fixed creator voice reference used by Seed-VC. "
        "The path is restricted to ComfyUI/input and must exist."
    )

    @classmethod
    def IS_CHANGED(cls, reference_path, **kwargs):
        input_root = Path(folder_paths.get_input_directory()).resolve()
        rel = str(reference_path or "").strip().replace("\\", "/")
        if not rel:
            return "missing"
        target = (input_root / rel).resolve()
        try:
            target.relative_to(input_root)
        except ValueError:
            return "invalid"
        if not target.is_file():
            return "missing"
        stat = target.stat()
        return f"{target}:{stat.st_size}:{stat.st_mtime_ns}"

    def load(self, reference_path):
        input_root = Path(folder_paths.get_input_directory()).resolve()
        rel = str(reference_path or "").strip().replace("\\", "/")
        if not rel:
            raise ValueError("Creator voice reference path is empty.")

        target = (input_root / rel).resolve()
        try:
            target.relative_to(input_root)
        except ValueError as exc:
            raise ValueError(
                "Creator voice reference must stay inside ComfyUI/input."
            ) from exc

        if not target.is_file():
            raise RuntimeError(
                "Creator voice reference is missing. Expected: "
                f"{target}. Add a clean reference.wav before running the workflow."
            )

        if target.suffix.lower() not in {".wav", ".flac", ".mp3", ".m4a", ".ogg"}:
            raise ValueError(
                "Unsupported creator voice reference format. "
                "Use WAV, FLAC, MP3, M4A or OGG; WAV is recommended."
            )

        try:
            waveform, sample_rate = torchaudio.load(str(target))
        except Exception as exc:
            raise RuntimeError(
                f"Could not decode creator voice reference: {target}"
            ) from exc

        if waveform.numel() == 0 or waveform.shape[-1] < max(1, int(sample_rate)):
            raise RuntimeError(
                "Creator voice reference is empty or shorter than one second."
            )

        # ComfyUI AUDIO uses [batch, channels, samples].
        audio = {
            "waveform": waveform.to(dtype=waveform.dtype).unsqueeze(0),
            "sample_rate": int(sample_rate),
        }
        duration = waveform.shape[-1] / float(sample_rate)
        status = (
            f"Loaded creator voice reference '{rel}' "
            f"({duration:.2f}s, {int(sample_rate)} Hz)."
        )
        return (audio, status)


NODE_CLASS_MAPPINGS = {
    "MeetMapCreatorVoiceReference": MeetMapCreatorVoiceReference,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeetMapCreatorVoiceReference": "MeetMap Creator Voice Reference",
}
