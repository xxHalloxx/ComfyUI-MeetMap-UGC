import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

import folder_paths


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _input_root() -> Path:
    return Path(folder_paths.get_input_directory()).resolve()


def _safe_target_folder(relative_folder: str) -> Path:
    root = _input_root()
    value = str(relative_folder).strip().replace("\\", "/").strip("/")
    folder = (root / value).resolve()
    try:
        folder.relative_to(root)
    except ValueError as exc:
        raise ValueError("target folder must stay inside ComfyUI/input") from exc
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _request_json(url: str):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "MeetMap-ComfyUI-UGC/2.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _download(url: str, target: Path):
    headers = {"User-Agent": "MeetMap-ComfyUI-UGC/2.0"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    if not data:
        raise RuntimeError(f"GitHub returned an empty file for {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


class MeetMapGitHubReferenceSync:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "owner": ("STRING", {"default": "xxHalloxx"}),
                "repo": ("STRING", {"default": "ComfyUI-MeetMap-UGC"}),
                "git_ref": ("STRING", {"default": "main"}),
                "creator_id": ("STRING", {"default": "creator_01"}),
                "target_root": ("STRING", {"default": "meetmap_refs"}),
                "always_check_github": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("reference_folder", "status", "file_count")
    FUNCTION = "sync"
    CATEGORY = "MeetMap/UGC"
    DESCRIPTION = "Checks GitHub every run and downloads only new or changed creator-reference files into ComfyUI/input."

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def sync(self, owner, repo, git_ref, creator_id, target_root, always_check_github):
        owner = str(owner).strip()
        repo = str(repo).strip()
        git_ref = str(git_ref).strip() or "main"
        creator_id = str(creator_id).strip() or "creator_01"
        target_root = str(target_root).strip().strip("/") or "meetmap_refs"
        relative_folder = f"{target_root}/{creator_id}"
        target_folder = _safe_target_folder(relative_folder)
        state_path = target_folder / ".github_sync_state.json"

        old_state = {}
        if state_path.is_file():
            try:
                old_state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                old_state = {}

        if not bool(always_check_github) and old_state.get("files"):
            return (relative_folder, f"Using cached GitHub references from {relative_folder}.", len(old_state["files"]))

        repo_folder = f"refs/creators/{creator_id}"
        encoded_path = urllib.parse.quote(repo_folder, safe="/")
        encoded_ref = urllib.parse.quote(git_ref, safe="")
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{encoded_path}?ref={encoded_ref}"
        listing = _request_json(api_url)
        if not isinstance(listing, list):
            raise RuntimeError(f"Unexpected GitHub contents response for {repo_folder}")

        remote = {}
        for item in listing:
            if not isinstance(item, dict) or item.get("type") != "file":
                continue
            name = str(item.get("name", ""))
            suffix = Path(name).suffix.lower()
            if suffix not in SUPPORTED_EXTENSIONS and name != "profile.json":
                continue
            download_url = item.get("download_url")
            sha = item.get("sha")
            if name and download_url and sha:
                remote[name] = {"sha": sha, "download_url": download_url}

        if not remote:
            raise RuntimeError(f"No usable creator references found in GitHub folder {repo_folder}")

        old_files = old_state.get("files", {}) if isinstance(old_state, dict) else {}
        downloaded = []
        for name, info in remote.items():
            target = target_folder / name
            previous = old_files.get(name, {})
            previous_sha = previous.get("sha") if isinstance(previous, dict) else None
            if previous_sha != info["sha"] or not target.is_file() or target.stat().st_size <= 0:
                _download(info["download_url"], target)
                downloaded.append(name)

        for child in target_folder.iterdir():
            if not child.is_file() or child.name.startswith("."):
                continue
            if (child.suffix.lower() in SUPPORTED_EXTENSIONS or child.name == "profile.json") and child.name not in remote:
                child.unlink(missing_ok=True)

        state = {
            "owner": owner,
            "repo": repo,
            "git_ref": git_ref,
            "creator_id": creator_id,
            "files": {name: {"sha": info["sha"]} for name, info in remote.items()},
        }
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        image_count = sum(1 for name in remote if Path(name).suffix.lower() in SUPPORTED_EXTENSIONS)
        status = (
            f"GitHub refs checked; downloaded/updated {len(downloaded)} file(s). {image_count} image refs active."
            if downloaded
            else f"GitHub refs checked; already current. {image_count} image refs active."
        )
        print("[MeetMap UGC] " + status)
        return (relative_folder, status, image_count)


class MeetMapReferenceSlots:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "references": ("MEETMAP_REFERENCE_SET",),
                "image_1_name": ("STRING", {"default": "face_front.png"}),
                "image_2_name": ("STRING", {"default": "face_angle.png"}),
                "image_3_name": ("STRING", {"default": "upper_body.png"}),
                "image_4_name": ("STRING", {"default": "skin_closeup.png"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE", "IMAGE", "STRING")
    RETURN_NAMES = ("image_1", "image_2", "image_3", "image_4", "status")
    FUNCTION = "select"
    CATEGORY = "MeetMap/UGC"
    DESCRIPTION = "Maps the creator reference set into four deterministic Qwen Image 2.1 image slots."

    def select(self, references, image_1_name, image_2_name, image_3_name, image_4_name):
        refs = list(references or [])
        if not refs:
            raise ValueError("No creator reference images are available. GitHub sync/loader must run first.")

        by_name = {}
        fallback = None
        for item in refs:
            if not isinstance(item, dict) or item.get("image") is None:
                continue
            image = item["image"]
            if fallback is None:
                fallback = image
            filename = str(item.get("filename", "")).replace("\\", "/")
            by_name[filename.lower()] = image
            by_name[Path(filename).name.lower()] = image

        if fallback is None:
            raise ValueError("Creator reference set contains no usable IMAGE tensors.")

        selected = []
        used = []
        for name in (image_1_name, image_2_name, image_3_name, image_4_name):
            key = Path(str(name).strip()).name.lower()
            image = by_name.get(key)
            if image is None:
                image = fallback
                used.append(f"{key}:fallback")
            else:
                used.append(key)
            selected.append(image)

        return (*selected, "Qwen Image 2.1 reference slots: " + ", ".join(used))


NODE_CLASS_MAPPINGS = {
    "MeetMapGitHubReferenceSync": MeetMapGitHubReferenceSync,
    "MeetMapReferenceSlots": MeetMapReferenceSlots,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeetMapGitHubReferenceSync": "MeetMap GitHub Reference Sync",
    "MeetMapReferenceSlots": "MeetMap Qwen Reference Slots",
}
