import io
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import folder_paths


_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
_DEFAULT_QUEUE_FOLDER_ID = "1oVcbIvv3w3FJK6VTunFzKMW4QRx4UIxf"
_DEFAULT_PROCESSED_FOLDER_ID = "1hbZ44M0_TVxdyho4F-HxkQvPw_rkonB6"
_DEFAULT_PARENT_FOLDER_ID = "1zqe7b5Nx06cJRE6MgaWomhRAv3rbZKAi"
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
_PROCESSED_KEY = "meetmap_processed"
_PROCESSED_AT_KEY = "meetmap_processed_at"
_CLAIM_ID_KEY = "meetmap_claim_id"
_CLAIMED_AT_KEY = "meetmap_claimed_at"


def _utc_now():
    return datetime.now(timezone.utc)


def _iso_utc(value=None):
    return (value or _utc_now()).isoformat().replace("+00:00", "Z")


def _validate_service_account_info(info):
    if isinstance(info, str):
        info = json.loads(info)
    if not isinstance(info, dict):
        raise ValueError("Service-account credential must decode to a JSON object.")
    required = ("type", "client_email", "private_key", "token_uri")
    missing = [key for key in required if not str(info.get(key) or "").strip()]
    if missing:
        raise ValueError("Service-account JSON is missing: " + ", ".join(missing))
    if info.get("type") != "service_account":
        raise ValueError("Credential JSON is not a Google service-account key.")
    return info


def _credentials():
    """Load service-account credentials from RunPod secret JSON or a protected local file."""
    try:
        from google.oauth2 import service_account
    except ImportError as exc:
        raise RuntimeError(
            "Google Drive dependencies are missing. Re-run the MeetMap installer so "
            "google-api-python-client and google-auth are installed."
        ) from exc

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    explicit_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()

    if raw:
        try:
            info = _validate_service_account_info(json.loads(raw))
        except Exception:
            # Some secret UIs store the entire JSON as an escaped JSON string.
            try:
                info = _validate_service_account_info(json.loads(json.loads(raw)))
            except Exception as exc:
                raise RuntimeError(
                    "GOOGLE_SERVICE_ACCOUNT_JSON could not be decoded as a service-account JSON object."
                ) from exc
        return service_account.Credentials.from_service_account_info(info, scopes=[_DRIVE_SCOPE])

    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())
    candidates.extend(
        [
            Path("/runpod-volume/secrets/google-service-account.json"),
            Path("/workspace/secrets/google-service-account.json"),
            Path("/workspace/google-service-account.json"),
            Path.home() / ".config/meetmap/google-service-account.json",
        ]
    )

    checked = []
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in checked:
            continue
        checked.append(candidate)
        if not candidate.is_file():
            continue
        try:
            info = _validate_service_account_info(
                json.loads(candidate.read_text(encoding="utf-8"))
            )
            return service_account.Credentials.from_service_account_info(
                info,
                scopes=[_DRIVE_SCOPE],
            )
        except Exception as exc:
            if explicit_path and candidate == Path(explicit_path).expanduser().resolve():
                raise RuntimeError(
                    f"GOOGLE_SERVICE_ACCOUNT_FILE is invalid: {candidate}: {exc}"
                ) from exc

    raise RuntimeError(
        "Google Drive credentials are not configured. Set GOOGLE_SERVICE_ACCOUNT_JSON "
        "or GOOGLE_SERVICE_ACCOUNT_FILE, or place google-service-account.json in "
        "/runpod-volume/secrets/ or /workspace/secrets/."
    )


def _drive():
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "google-api-python-client is missing. Re-run the MeetMap installer."
        ) from exc
    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)


def _resolve_folder_id(value, env_name):
    folder_id = str(value or "").strip() or os.environ.get(env_name, "").strip()
    if not folder_id:
        raise ValueError(
            f"Google Drive folder id is empty. Set the node field or {env_name}."
        )
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,}", folder_id):
        raise ValueError("Google Drive folder id has an unexpected format.")
    return folder_id


def _validate_folder_target(service, folder_id, required_folder_name="", required_parent_folder_name=""):
    """Fail closed unless the configured source is the intended Drive queue path."""
    metadata = (
        service.files()
        .get(
            fileId=folder_id,
            fields="id,name,mimeType,parents",
            supportsAllDrives=True,
        )
        .execute(num_retries=3)
    )
    if metadata.get("mimeType") != "application/vnd.google-apps.folder":
        raise ValueError("Configured Google Drive source is not a folder.")

    expected_name = str(required_folder_name or "").strip()
    if expected_name and str(metadata.get("name") or "").strip() != expected_name:
        raise RuntimeError(
            f"Refusing Drive source folder '{metadata.get('name')}'. Expected '{expected_name}'."
        )

    expected_parent_name = str(required_parent_folder_name or "").strip()
    if expected_parent_name:
        parent_ids = [str(value) for value in (metadata.get("parents") or []) if str(value).strip()]
        if not parent_ids:
            raise RuntimeError(
                f"Refusing Drive source folder '{metadata.get('name')}' because it has no parent."
            )
        parent_names = []
        for parent_id in parent_ids:
            parent = (
                service.files()
                .get(
                    fileId=parent_id,
                    fields="id,name,mimeType",
                    supportsAllDrives=True,
                )
                .execute(num_retries=3)
            )
            parent_names.append(str(parent.get("name") or "").strip())
        if expected_parent_name not in parent_names:
            raise RuntimeError(
                f"Refusing Drive source folder '{metadata.get('name')}'. "
                f"Expected parent '{expected_parent_name}', got {parent_names or ['<unknown>']}."
            )
    return metadata


def _find_sibling_folder(service, source_folder_id, sibling_name, required_parent_folder_name=""):
    """Resolve a sibling folder next to source_folder_id under the same parent."""
    source = (
        service.files()
        .get(
            fileId=source_folder_id,
            fields="id,name,mimeType,parents",
            supportsAllDrives=True,
        )
        .execute(num_retries=3)
    )
    if source.get("mimeType") != "application/vnd.google-apps.folder":
        raise RuntimeError("Source parent is not a Google Drive folder.")

    parent_ids = [str(value) for value in (source.get("parents") or []) if str(value).strip()]
    if len(parent_ids) != 1:
        raise RuntimeError(
            f"Expected Queue to have exactly one parent, got {len(parent_ids)}."
        )
    root_parent_id = parent_ids[0]
    root_parent = (
        service.files()
        .get(
            fileId=root_parent_id,
            fields="id,name,mimeType",
            supportsAllDrives=True,
        )
        .execute(num_retries=3)
    )
    expected_parent_name = str(required_parent_folder_name or "").strip()
    if expected_parent_name and str(root_parent.get("name") or "").strip() != expected_parent_name:
        raise RuntimeError(
            f"Refusing processed-folder lookup. Expected parent '{expected_parent_name}', "
            f"got '{root_parent.get('name')}'."
        )

    escaped_name = str(sibling_name or "").replace("\\", "\\\\").replace("'", "\\'")
    response = (
        service.files()
        .list(
            q=(
                f"'{root_parent_id}' in parents and trashed = false and "
                "mimeType = 'application/vnd.google-apps.folder' and "
                f"name = '{escaped_name}'"
            ),
            spaces="drive",
            fields="files(id,name,mimeType,parents)",
            pageSize=10,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        .execute(num_retries=3)
    )
    matches = response.get("files") or []
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one sibling folder named '{sibling_name}' under "
            f"'{root_parent.get('name')}', found {len(matches)}."
        )
    return matches[0]


def _safe_filename(name):
    cleaned = Path(str(name or "reference_video.mp4")).name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", cleaned).strip(" .")
    return cleaned or "reference_video.mp4"


def _parse_drive_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _claim_active(props, ttl_minutes):
    claim_id = str((props or {}).get(_CLAIM_ID_KEY, "")).strip()
    claimed_at = _parse_drive_time((props or {}).get(_CLAIMED_AT_KEY))
    if not claim_id or claimed_at is None:
        return False
    age = (_utc_now() - claimed_at.astimezone(timezone.utc)).total_seconds()
    return 0 <= age < max(1, int(ttl_minutes)) * 60


def _merge_app_properties(service, file_id, updates):
    current = (
        service.files()
        .get(
            fileId=file_id,
            fields="id,appProperties",
            supportsAllDrives=True,
        )
        .execute(num_retries=3)
    )
    props = dict(current.get("appProperties") or {})
    for key, value in updates.items():
        props[str(key)] = "" if value is None else str(value)
    (
        service.files()
        .update(
            fileId=file_id,
            body={"appProperties": props},
            fields="id,appProperties,parents",
            supportsAllDrives=True,
        )
        .execute(num_retries=3)
    )
    return props


def _is_video_candidate(item):
    name = str(item.get("name") or "")
    mime = str(item.get("mimeType") or "")
    return mime.startswith("video/") or Path(name).suffix.lower() in _VIDEO_EXTENSIONS


def _normalize_video_for_comfy(path):
    """Best-effort container/codec normalization. Never destroys the original download."""
    source = Path(path)
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    notes = []

    needs_normalize = source.suffix.lower() not in {".mp4", ".m4v"}
    if ffprobe:
        try:
            probe = subprocess.run(
                [
                    ffprobe,
                    "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=codec_name,width,height",
                    "-of", "default=noprint_wrappers=1",
                    str(source),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            if probe.returncode != 0 or "width=" not in probe.stdout or "height=" not in probe.stdout:
                needs_normalize = True
                notes.append("ffprobe requested normalization")
        except Exception as exc:
            notes.append(f"ffprobe unavailable/failed: {exc}")

    if not needs_normalize:
        return source, "source container accepted directly"

    if not ffmpeg:
        return source, "WARNING: source would benefit from normalization but ffmpeg is unavailable; using original"

    normalized = source.with_name(source.stem + ".meetmap_normalized.mp4")
    if normalized.is_file() and normalized.stat().st_size > 0 and normalized.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return normalized, "reused existing normalized MP4 fallback"

    normalized.unlink(missing_ok=True)
    commands = [
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(normalized),
        ],
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(normalized),
        ],
    ]

    errors = []
    for attempt, command in enumerate(commands, start=1):
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=900,
            )
            if result.returncode == 0 and normalized.is_file() and normalized.stat().st_size > 0:
                return normalized, f"normalized source to MP4 using ffmpeg fallback #{attempt}"
            errors.append(result.stderr.strip() or f"ffmpeg exit={result.returncode}")
        except Exception as exc:
            errors.append(str(exc))
        normalized.unlink(missing_ok=True)

    return source, "WARNING: ffmpeg normalization failed; using original source. " + " | ".join(errors)


class MeetMapGoogleDriveLatestVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "folder_id": (
                    "STRING",
                    {
                        "default": os.environ.get("MEETMAP_MOTION_DRIVE_FOLDER_ID", _DEFAULT_QUEUE_FOLDER_ID),
                        "multiline": False,
                    },
                ),
                "required_folder_name": (
                    "STRING",
                    {
                        "default": os.environ.get(
                            "MEETMAP_MOTION_EXPECTED_FOLDER_NAME",
                            "Queue",
                        ),
                        "multiline": False,
                    },
                ),
                "required_parent_folder_name": (
                    "STRING",
                    {
                        "default": os.environ.get(
                            "MEETMAP_MOTION_EXPECTED_PARENT_FOLDER_NAME",
                            "MeetMap TikTok Content",
                        ),
                        "multiline": False,
                    },
                ),
                "claim_mode": (
                    ["claim_required", "read_only"],
                    {"default": "claim_required"},
                ),
                "claim_ttl_minutes": (
                    "INT",
                    {"default": 180, "min": 15, "max": 1440, "step": 15},
                ),
                "max_file_size_mb": (
                    "INT",
                    {"default": 1000, "min": 10, "max": 20000, "step": 10},
                ),
                "download_subfolder": (
                    "STRING",
                    {"default": "meetmap_drive", "multiline": False},
                ),
            }
        }

    RETURN_TYPES = ("VIDEO", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "video",
        "file_id",
        "claim_token",
        "drive_filename",
        "local_filename",
        "status",
    )
    FUNCTION = "load"
    CATEGORY = "MeetMap/Automation"
    DESCRIPTION = (
        "Downloads only the newest unprocessed video from the verified Google Drive Queue folder, "
        "optionally leases/claims it to prevent duplicate concurrent renders, and returns a native "
        "ComfyUI VIDEO."
    )

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # The Drive queue is external state: always re-check it for a new workflow run.
        return float("nan")

    def load(
        self,
        folder_id,
        required_folder_name,
        required_parent_folder_name,
        claim_mode,
        claim_ttl_minutes,
        max_file_size_mb,
        download_subfolder,
    ):
        try:
            from googleapiclient.http import MediaIoBaseDownload
            from comfy_api.latest import InputImpl
        except ImportError as exc:
            raise RuntimeError(
                "Required Google Drive / modern ComfyUI video APIs are unavailable. "
                "Re-run the installer and update ComfyUI."
            ) from exc

        folder_id = str(folder_id or "").strip() or os.environ.get(
            "MEETMAP_MOTION_DRIVE_FOLDER_ID", _DEFAULT_QUEUE_FOLDER_ID
        ).strip() or _DEFAULT_QUEUE_FOLDER_ID
        folder_id = _resolve_folder_id(folder_id, "MEETMAP_MOTION_DRIVE_FOLDER_ID")
        service = _drive()
        source_folder = _validate_folder_target(
            service,
            folder_id,
            required_folder_name=required_folder_name,
            required_parent_folder_name=required_parent_folder_name,
        )

        candidates = []
        page_token = None
        pages_checked = 0
        while pages_checked < 10 and not candidates:
            response = (
                service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    spaces="drive",
                    fields=(
                        "nextPageToken,files("
                        "id,name,mimeType,size,modifiedTime,createdTime,appProperties,parents)"
                    ),
                    orderBy="modifiedTime desc",
                    pageSize=100,
                    pageToken=page_token,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                )
                .execute(num_retries=3)
            )
            pages_checked += 1
            for item in response.get("files") or []:
                if not _is_video_candidate(item):
                    continue
                props = item.get("appProperties") or {}
                if str(props.get(_PROCESSED_KEY, "")).lower() == "true":
                    continue
                if _claim_active(props, claim_ttl_minutes):
                    continue
                candidates.append(item)
                break
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        if not candidates:
            raise RuntimeError(
                "No new unprocessed/unclaimed video was found in the verified Google Drive Queue folder."
            )

        chosen = candidates[0]
        file_id = str(chosen["id"])
        drive_name = _safe_filename(chosen.get("name"))
        size = int(chosen.get("size") or 0)
        max_bytes = int(max_file_size_mb) * 1024 * 1024
        if size > max_bytes > 0:
            raise RuntimeError(
                f"Newest Drive video is {size / (1024*1024):.1f} MB, above the "
                f"{int(max_file_size_mb)} MB safety limit."
            )

        claim_token = ""
        if str(claim_mode) == "claim_required":
            claim_token = uuid.uuid4().hex
            _merge_app_properties(
                service,
                file_id,
                {
                    _CLAIM_ID_KEY: claim_token,
                    _CLAIMED_AT_KEY: _iso_utc(),
                },
            )

        input_root = Path(folder_paths.get_input_directory()).resolve()
        subfolder = re.sub(r"[^A-Za-z0-9._/-]+", "_", str(download_subfolder or "meetmap_drive"))
        subfolder = subfolder.strip("/").replace("..", "_") or "meetmap_drive"
        destination_dir = (input_root / subfolder).resolve()
        try:
            destination_dir.relative_to(input_root)
        except ValueError as exc:
            raise ValueError("download_subfolder must stay inside ComfyUI/input.") from exc
        destination_dir.mkdir(parents=True, exist_ok=True)

        local_name = f"{file_id}_{drive_name}"
        target = (destination_dir / local_name).resolve()
        partial = target.with_suffix(target.suffix + ".part")

        # A previously downloaded file may be reused only if its byte size still matches Drive.
        reuse = target.is_file() and (size <= 0 or target.stat().st_size == size)
        if not reuse:
            request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
            try:
                with partial.open("wb") as handle:
                    downloader = MediaIoBaseDownload(handle, request, chunksize=8 * 1024 * 1024)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk(num_retries=3)
                if size > 0 and partial.stat().st_size != size:
                    raise RuntimeError(
                        f"Drive download size mismatch: expected {size}, got {partial.stat().st_size}."
                    )
                partial.replace(target)
            except Exception:
                partial.unlink(missing_ok=True)
                if claim_token:
                    try:
                        _merge_app_properties(
                            service,
                            file_id,
                            {_CLAIM_ID_KEY: "", _CLAIMED_AT_KEY: ""},
                        )
                    except Exception:
                        pass
                raise

        normalized_target, normalize_status = _normalize_video_for_comfy(target)
        try:
            video = InputImpl.VideoFromFile(str(normalized_target))
        except Exception as first_exc:
            # If ComfyUI rejects the normalized file object construction, retry the exact
            # original download once before giving up. This covers codec/container edge cases.
            if normalized_target != target:
                try:
                    video = InputImpl.VideoFromFile(str(target))
                    normalized_target = target
                    normalize_status += f"; normalized VideoFromFile failed ({first_exc}); original accepted"
                except Exception as second_exc:
                    if claim_token:
                        try:
                            _merge_app_properties(
                                service,
                                file_id,
                                {_CLAIM_ID_KEY: "", _CLAIMED_AT_KEY: ""},
                            )
                        except Exception:
                            pass
                    raise RuntimeError(
                        f"Downloaded Drive video could not be opened by ComfyUI: "
                        f"normalized={first_exc}; original={second_exc}"
                    ) from second_exc
            else:
                if claim_token:
                    try:
                        _merge_app_properties(
                            service,
                            file_id,
                            {_CLAIM_ID_KEY: "", _CLAIMED_AT_KEY: ""},
                        )
                    except Exception:
                        pass
                raise RuntimeError(f"Downloaded Drive video could not be opened by ComfyUI: {first_exc}") from first_exc

        relative = normalized_target.relative_to(input_root).as_posix()
        status = (
            f"Drive queue '{source_folder.get('name', 'Queue')}' selected new video "
            f"'{drive_name}' ({file_id}); "
            f"{'claimed' if claim_token else 'read-only'}; local={relative}; {normalize_status}."
        )
        return (video, file_id, claim_token, drive_name, relative, status)


class MeetMapGoogleDriveMarkProcessed:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video": ("VIDEO",),
                "file_id": ("STRING", {"forceInput": True}),
                "claim_token": ("STRING", {"forceInput": True}),
                "mark_processed": ("BOOLEAN", {"default": True}),
                "processed_folder_id": (
                    "STRING",
                    {
                        "default": os.environ.get(
                            "MEETMAP_MOTION_PROCESSED_FOLDER_ID",
                            _DEFAULT_PROCESSED_FOLDER_ID,
                        ),
                        "multiline": False,
                    },
                ),
                "processed_folder_name": (
                    "STRING",
                    {
                        "default": os.environ.get(
                            "MEETMAP_MOTION_PROCESSED_FOLDER_NAME",
                            "Already posted",
                        ),
                        "multiline": False,
                    },
                ),
                "required_parent_folder_name": (
                    "STRING",
                    {
                        "default": os.environ.get(
                            "MEETMAP_MOTION_EXPECTED_PARENT_FOLDER_NAME",
                            "MeetMap TikTok Content",
                        ),
                        "multiline": False,
                    },
                ),
            }
        }

    RETURN_TYPES = ("VIDEO", "STRING")
    RETURN_NAMES = ("video", "status")
    FUNCTION = "mark"
    OUTPUT_NODE = True
    CATEGORY = "MeetMap/Automation"
    DESCRIPTION = (
        "Runs after the final SaveVideo node. Marks the source Drive video processed and moves "
        "it from Queue into the sibling 'Already posted' folder by default. The claim token "
        "prevents another run from finalizing a file it did not claim."
    )

    def mark(
        self,
        video,
        file_id,
        claim_token,
        mark_processed,
        processed_folder_id,
        processed_folder_name,
        required_parent_folder_name,
    ):
        if not bool(mark_processed):
            return (video, "Drive source left unchanged (mark_processed=false).")

        file_id = str(file_id or "").strip()
        if not file_id:
            raise ValueError("file_id is empty.")

        service = _drive()
        metadata = (
            service.files()
            .get(
                fileId=file_id,
                fields="id,name,parents,appProperties",
                supportsAllDrives=True,
            )
            .execute(num_retries=3)
        )
        props = dict(metadata.get("appProperties") or {})
        current_claim = str(props.get(_CLAIM_ID_KEY, "")).strip()
        supplied_claim = str(claim_token or "").strip()
        if (current_claim or supplied_claim) and current_claim != supplied_claim:
            raise RuntimeError(
                "Google Drive claim token mismatch. Refusing to mark another workflow run's file processed."
            )

        move_target = (
            str(processed_folder_id or "").strip()
            or os.environ.get("MEETMAP_MOTION_PROCESSED_FOLDER_ID", "").strip()
            or _DEFAULT_PROCESSED_FOLDER_ID
        )
        source_parents = [
            str(value)
            for value in (metadata.get("parents") or [])
            if str(value).strip()
        ]
        sibling_name = str(processed_folder_name or "").strip()

        if not move_target and sibling_name:
            if len(source_parents) != 1:
                raise RuntimeError(
                    f"Expected processed source to have exactly one Queue parent, got {len(source_parents)}."
                )
            sibling = _find_sibling_folder(
                service,
                source_parents[0],
                sibling_name,
                required_parent_folder_name=required_parent_folder_name,
            )
            move_target = str(sibling["id"])

        if not move_target:
            raise RuntimeError(
                "No processed destination folder could be resolved. "
                "Refusing to mark the source processed while it is still in Queue."
            )

        move_target = _resolve_folder_id(
            move_target,
            "MEETMAP_MOTION_PROCESSED_FOLDER_ID",
        )
        remove = ",".join(source_parents)
        kwargs = {
            "fileId": file_id,
            "addParents": move_target,
            "fields": "id,parents",
            "supportsAllDrives": True,
        }
        if remove:
            kwargs["removeParents"] = remove

        # Move first. If this fails, the source remains in Queue and is not marked processed.
        service.files().update(**kwargs).execute(num_retries=3)

        # Only finalize the processed state after the file has left Queue successfully.
        _merge_app_properties(
            service,
            file_id,
            {
                _PROCESSED_KEY: "true",
                _PROCESSED_AT_KEY: _iso_utc(),
                _CLAIM_ID_KEY: "",
                _CLAIMED_AT_KEY: "",
            },
        )

        status = (
            f"Drive source moved from Queue to "
            f"'{sibling_name or 'processed'}' ({move_target}) and marked processed."
        )

        return (video, status)




class MeetMapGoogleDriveFinalizeSafe(MeetMapGoogleDriveMarkProcessed):
    FUNCTION = "mark_safe"
    DESCRIPTION = (
        "Fail-soft finalizer. The rendered video is never invalidated by a later Drive move/"
        "metadata failure. On failure it returns the video unchanged and reports a warning; "
        "the Drive source remains unprocessed/retryable."
    )

    def mark_safe(
        self,
        video,
        file_id,
        claim_token,
        mark_processed,
        processed_folder_id,
        processed_folder_name,
        required_parent_folder_name,
    ):
        try:
            return super().mark(
                video,
                file_id,
                claim_token,
                mark_processed,
                processed_folder_id,
                processed_folder_name,
                required_parent_folder_name,
            )
        except Exception as exc:
            status = (
                "WARNING: final video was already produced, but Google Drive finalization failed. "
                "Source was NOT marked processed by this fallback and should remain safe for manual/"
                f"later recovery. {type(exc).__name__}: {exc}"
            )
            print("[MeetMap Drive] " + status)
            return (video, status)


NODE_CLASS_MAPPINGS = {
    "MeetMapGoogleDriveLatestVideo": MeetMapGoogleDriveLatestVideo,
    "MeetMapGoogleDriveMarkProcessed": MeetMapGoogleDriveMarkProcessed,
    "MeetMapGoogleDriveFinalizeSafe": MeetMapGoogleDriveFinalizeSafe,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeetMapGoogleDriveLatestVideo": "MeetMap Google Drive Latest Video",
    "MeetMapGoogleDriveMarkProcessed": "MeetMap Google Drive Mark Processed",
    "MeetMapGoogleDriveFinalizeSafe": "MeetMap Google Drive Finalize Safe",
}
