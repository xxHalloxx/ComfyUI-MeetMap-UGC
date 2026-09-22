import hashlib
import os
import re
from pathlib import Path

import folder_paths
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
        .execute()
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
                _, done = downloader.next_chunk()
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
            target.unlink(missing_ok=True)
            raise RuntimeError(
                "Creator voice reference SHA-256 mismatch. The downloaded/local audio is not "
                "the approved creator_01 voice version."
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
            f"{int(sample_rate)} Hz, {source_note}, sha256={actual_hash[:12]}...)."
        )
        if drive_name:
            status += f" Drive source='{drive_name}'."
        return (audio, status)


NODE_CLASS_MAPPINGS = {
    "MeetMapCreatorVoiceReference": MeetMapCreatorVoiceReference,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeetMapCreatorVoiceReference": "MeetMap Creator Voice Reference",
}
