import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import folder_paths
import soundfile as sf
import torch
import torchaudio

from .gdrive_nodes import _drive


_DEFAULT_VOICE_DRIVE_FILE_ID = "1BjZUeye3fVAkA1DtvdlYdsNQzMY_gANt"
_DEFAULT_VOICE_SHA256 = "e0c502c490c74bbda5226fae8fb95206bb6facf1eb2d1d2ff025b19f9ae62fb7"
_LEGACY_REFERENCE_PATH = "meetmap_refs/creator_01/voice/reference.wav"
_DEFAULT_REFERENCE_PATH = "meetmap_refs/creator_01/voice/reference.flac"


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode_audio_file(path):
    path = Path(path)
    errors = []

    try:
        waveform, sample_rate = torchaudio.load(str(path))
        return waveform, int(sample_rate), "torchaudio"
    except Exception as exc:
        errors.append(f"torchaudio: {exc}")

    try:
        samples, sample_rate = sf.read(
            str(path),
            dtype="float32",
            always_2d=True,
        )
        waveform = torch.from_numpy(samples.T.copy())
        return waveform, int(sample_rate), "soundfile"
    except Exception as exc:
        errors.append(f"soundfile: {exc}")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        decoded = path.with_suffix(path.suffix + ".decoded.wav")
        decoded.unlink(missing_ok=True)
        try:
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "44100",
                    "-c:a",
                    "pcm_s16le",
                    str(decoded),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0 or not decoded.is_file() or decoded.stat().st_size <= 44:
                raise RuntimeError(proc.stderr.strip() or f"ffmpeg exit code {proc.returncode}")
            samples, sample_rate = sf.read(
                str(decoded),
                dtype="float32",
                always_2d=True,
            )
            waveform = torch.from_numpy(samples.T.copy())
            return waveform, int(sample_rate), "ffmpeg+soundfile"
        except Exception as exc:
            errors.append(f"ffmpeg: {exc}")
        finally:
            decoded.unlink(missing_ok=True)
    else:
        errors.append("ffmpeg: binary not found")

    raise RuntimeError("All creator voice decoders failed. " + " | ".join(errors))


def _cleanup_cuda_best_effort():
    notes = []
    try:
        import comfy.model_management as model_management
        try:
            model_management.unload_all_models()
            notes.append("unload_all_models")
        except Exception:
            pass
        try:
            model_management.soft_empty_cache()
            notes.append("soft_empty_cache")
        except Exception:
            pass
    except Exception:
        pass

    if torch.cuda.is_available():
        try:
            torch.cuda.empty_cache()
            notes.append("cuda.empty_cache")
        except Exception:
            pass
    return notes


_SEEDVC_MODULE = None


def _load_seedvc_module():
    global _SEEDVC_MODULE
    if _SEEDVC_MODULE is not None:
        return _SEEDVC_MODULE

    base = Path(getattr(folder_paths, "base_path", Path(folder_paths.__file__).resolve().parent))
    candidate = base / "custom_nodes" / "ComfyUI_Seed-VC" / "seedvcnode.py"
    if not candidate.is_file():
        raise RuntimeError(f"Seed-VC node file not found: {candidate}")

    seed_dir = str(candidate.parent)
    if seed_dir not in sys.path:
        sys.path.insert(0, seed_dir)

    spec = importlib.util.spec_from_file_location("_meetmap_seedvc_runtime", candidate)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not create Seed-VC import spec for {candidate}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "SeedVCRun"):
        raise RuntimeError("Seed-VC module does not expose SeedVCRun.")
    _SEEDVC_MODULE = module
    return module


def _safe_input_target(reference_path):
    input_root = Path(folder_paths.get_input_directory()).resolve()
    rel = str(reference_path or "").strip().replace("\\", "/")
    if rel == _LEGACY_REFERENCE_PATH:
        rel = _DEFAULT_REFERENCE_PATH
    if not rel:
        raise ValueError("Creator voice reference path is empty.")

    target = (input_root / rel).resolve()
    try:
        target.relative_to(input_root)
    except ValueError as exc:
        raise ValueError(
            "Creator voice reference must stay inside ComfyUI/input."
        ) from exc
    return input_root, rel, target


def _download_drive_reference(file_id, target):
    try:
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError as exc:
        raise RuntimeError(
            "Google Drive download support is missing. Re-run the MeetMap installer."
        ) from exc

    file_id = str(file_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,}", file_id):
        raise ValueError("Creator voice Drive file ID has an unexpected format.")

    service = _drive()
    metadata = (
        service.files()
        .get(
            fileId=file_id,
            fields="id,name,mimeType,size,modifiedTime",
            supportsAllDrives=True,
        )
        .execute(num_retries=3)
    )
    if metadata.get("mimeType") == "application/vnd.google-apps.folder":
        raise RuntimeError("Creator voice Drive file ID points to a folder, not audio.")

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    partial.unlink(missing_ok=True)
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    try:
        with partial.open("wb") as handle:
            downloader = MediaIoBaseDownload(
                handle,
                request,
                chunksize=4 * 1024 * 1024,
            )
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=3)
        expected_size = int(metadata.get("size") or 0)
        if expected_size and partial.stat().st_size != expected_size:
            raise RuntimeError(
                "Creator voice Drive download size mismatch: "
                f"expected {expected_size}, got {partial.stat().st_size}."
            )
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return metadata


class MeetMapCreatorVoiceReference:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "reference_path": (
                    "STRING",
                    {
                        "default": _DEFAULT_REFERENCE_PATH,
                        "multiline": False,
                    },
                ),
            },
            "optional": {
                "drive_file_id": (
                    "STRING",
                    {
                        "default": os.environ.get(
                            "MEETMAP_CREATOR_VOICE_DRIVE_FILE_ID",
                            _DEFAULT_VOICE_DRIVE_FILE_ID,
                        ),
                        "multiline": False,
                    },
                ),
                "expected_sha256": (
                    "STRING",
                    {
                        "default": os.environ.get(
                            "MEETMAP_CREATOR_VOICE_SHA256",
                            _DEFAULT_VOICE_SHA256,
                        ),
                        "multiline": False,
                    },
                ),
                "download_if_missing": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("AUDIO", "STRING")
    RETURN_NAMES = ("audio", "status")
    FUNCTION = "load"
    CATEGORY = "MeetMap/Voice"
    DESCRIPTION = (
        "Loads the fixed creator voice used by Seed-VC. If the local FLAC is missing, "
        "it downloads the versioned reference from Google Drive and verifies SHA-256."
    )

    @classmethod
    def IS_CHANGED(
        cls,
        reference_path,
        drive_file_id=None,
        expected_sha256=None,
        download_if_missing=True,
        **kwargs,
    ):
        drive_file_id = drive_file_id or os.environ.get(
            "MEETMAP_CREATOR_VOICE_DRIVE_FILE_ID",
            _DEFAULT_VOICE_DRIVE_FILE_ID,
        )
        expected_sha256 = expected_sha256 or os.environ.get(
            "MEETMAP_CREATOR_VOICE_SHA256",
            _DEFAULT_VOICE_SHA256,
        )
        try:
            _, _, target = _safe_input_target(reference_path)
        except Exception:
            return "invalid"
        local_state = "missing"
        if target.is_file():
            stat = target.stat()
            local_state = f"{stat.st_size}:{stat.st_mtime_ns}"
        return (
            f"{target}:{local_state}:{str(drive_file_id).strip()}:"
            f"{str(expected_sha256).strip().lower()}:{bool(download_if_missing)}"
        )

    def load(
        self,
        reference_path,
        drive_file_id=None,
        expected_sha256=None,
        download_if_missing=True,
    ):
        drive_file_id = drive_file_id or os.environ.get(
            "MEETMAP_CREATOR_VOICE_DRIVE_FILE_ID",
            _DEFAULT_VOICE_DRIVE_FILE_ID,
        )
        expected_sha256 = expected_sha256 or os.environ.get(
            "MEETMAP_CREATOR_VOICE_SHA256",
            _DEFAULT_VOICE_SHA256,
        )

        _, rel, target = _safe_input_target(reference_path)
        expected_hash = str(expected_sha256 or "").strip().lower()
        if expected_hash and not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError("Creator voice expected_sha256 must be a 64-character hex digest.")

        downloaded = False
        metadata = None
        if not target.is_file():
            if not bool(download_if_missing):
                raise RuntimeError(
                    f"Creator voice reference is missing: {target}. "
                    "download_if_missing is disabled."
                )
            metadata = _download_drive_reference(drive_file_id, target)
            downloaded = True

        if target.suffix.lower() not in {".wav", ".flac", ".mp3", ".m4a", ".ogg"}:
            raise ValueError(
                "Unsupported creator voice reference format. "
                "Use WAV, FLAC, MP3, M4A or OGG."
            )

        actual_hash = _sha256(target)
        if expected_hash and actual_hash != expected_hash:
            if bool(download_if_missing):
                target.unlink(missing_ok=True)
                metadata = _download_drive_reference(drive_file_id, target)
                downloaded = True
                actual_hash = _sha256(target)
            if expected_hash and actual_hash != expected_hash:
                target.unlink(missing_ok=True)
                raise RuntimeError(
                    "Creator voice reference SHA-256 mismatch even after automatic redownload. "
                    "Refusing to use unapproved/corrupt creator audio."
                )

        try:
            waveform, sample_rate, decoder = _decode_audio_file(target)
        except Exception as first_exc:
            # If a local copy is somehow damaged despite the filename being present,
            # remove it, fetch the approved Drive asset one more time, re-verify the hash,
            # then run all three decoders again.
            if bool(download_if_missing):
                target.unlink(missing_ok=True)
                metadata = _download_drive_reference(drive_file_id, target)
                downloaded = True
                actual_hash = _sha256(target)
                if expected_hash and actual_hash != expected_hash:
                    target.unlink(missing_ok=True)
                    raise RuntimeError(
                        "Creator voice redownload succeeded but SHA-256 still mismatched."
                    ) from first_exc
                waveform, sample_rate, decoder = _decode_audio_file(target)
            else:
                raise RuntimeError(
                    f"Could not decode creator voice reference: {target}. {first_exc}"
                ) from first_exc

        if waveform.numel() == 0 or waveform.shape[-1] < max(1, int(sample_rate)):
            raise RuntimeError(
                "Creator voice reference is empty or shorter than one second."
            )

        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)

        audio = {
            "waveform": waveform.unsqueeze(0),
            "sample_rate": int(sample_rate),
        }
        duration = waveform.shape[-1] / float(sample_rate)
        source_note = "downloaded from Drive" if downloaded else "local verified copy"
        drive_name = str((metadata or {}).get("name") or "").strip()
        status = (
            f"Loaded creator_01 voice '{rel}' ({duration:.2f}s, "
            f"{int(sample_rate)} Hz, decoder={decoder}, {source_note}, sha256={actual_hash[:12]}...)."
        )
        if drive_name:
            status += f" Drive source='{drive_name}'."
        return (audio, status)


class MeetMapSeedVCWithFallback:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source_audio": ("AUDIO",),
                "ref_audio": ("AUDIO",),
                "steps": ("INT", {"default": 30, "min": 1, "max": 200, "step": 1}),
                "speed": ("FLOAT", {"default": 1.0, "min": 0.5, "max": 2.0, "step": 0.1}),
                "inference_cfg_rate": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 1.0, "step": 0.1}),
                "f0_condition": ("BOOLEAN", {"default": False}),
                "auto_f0_adjust": ("BOOLEAN", {"default": True}),
                "pitch_shift": ("INT", {"default": 0, "min": -24, "max": 24, "step": 1}),
                "unload_model": ("BOOLEAN", {"default": True}),
                "retry_once": ("BOOLEAN", {"default": True}),
                "fallback_to_source_audio": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("AUDIO", "STRING")
    RETURN_NAMES = ("audio", "status")
    FUNCTION = "run"
    CATEGORY = "MeetMap/Voice"
    DESCRIPTION = (
        "Runs Seed-VC with recovery. On failure it unloads/cleans VRAM and retries once "
        "with conservative settings. If conversion still fails it can pass through the "
        "original source audio so the completed video render is not lost."
    )

    def run(
        self,
        source_audio,
        ref_audio,
        steps,
        speed,
        inference_cfg_rate,
        f0_condition,
        auto_f0_adjust,
        pitch_shift,
        unload_model,
        retry_once,
        fallback_to_source_audio,
    ):
        failures = []
        module = None

        def reset_seedvc():
            nonlocal module
            if module is not None and hasattr(module, "SEEDVC"):
                try:
                    module.SEEDVC = None
                except Exception:
                    pass
            _cleanup_cuda_best_effort()

        try:
            module = _load_seedvc_module()
            runner = module.SeedVCRun()
            converted = runner.run(
                source_audio,
                ref_audio,
                int(steps),
                float(speed),
                float(inference_cfg_rate),
                bool(f0_condition),
                bool(auto_f0_adjust),
                int(pitch_shift),
                bool(unload_model),
            )[0]
            return (converted, "Seed-VC conversion succeeded on primary attempt.")
        except Exception as exc:
            failures.append(f"primary: {type(exc).__name__}: {exc}")
            reset_seedvc()

        if bool(retry_once):
            try:
                module = _load_seedvc_module()
                runner = module.SeedVCRun()
                safe_steps = max(10, min(int(steps), 20))
                converted = runner.run(
                    source_audio,
                    ref_audio,
                    safe_steps,
                    1.0,
                    min(float(inference_cfg_rate), 0.7),
                    False,
                    True,
                    0,
                    True,
                )[0]
                return (
                    converted,
                    "Seed-VC primary attempt failed; conservative retry succeeded "
                    f"(steps={safe_steps}, speed=1.0, f0=false, pitch=0). "
                    + failures[0],
                )
            except Exception as exc:
                failures.append(f"retry: {type(exc).__name__}: {exc}")
                reset_seedvc()

        if bool(fallback_to_source_audio):
            return (
                source_audio,
                "Seed-VC unavailable after recovery attempts; original source audio used. "
                + " | ".join(failures),
            )

        raise RuntimeError("Seed-VC failed after recovery attempts. " + " | ".join(failures))


NODE_CLASS_MAPPINGS = {
    "MeetMapCreatorVoiceReference": MeetMapCreatorVoiceReference,
    "MeetMapSeedVCWithFallback": MeetMapSeedVCWithFallback,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeetMapCreatorVoiceReference": "MeetMap Creator Voice Reference",
    "MeetMapSeedVCWithFallback": "MeetMap Seed-VC With Fallback",
}
