import os
from pathlib import Path

import folder_paths


class MeetMapSafeSaveVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video": ("VIDEO",),
                "filename_prefix": (
                    "STRING",
                    {"default": "video/meetmap_scail2_character_swap_v5"},
                ),
            }
        }

    RETURN_TYPES = ("VIDEO", "STRING")
    RETURN_NAMES = ("video", "status")
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "MeetMap/Runtime"
    DESCRIPTION = (
        "Saves the final VIDEO with codec/container fallbacks. A single encoder failure "
        "does not invalidate the completed render; multiple formats are tried in order."
    )

    def save(self, video, filename_prefix):
        try:
            from comfy_api.latest import Types
        except Exception as exc:
            raise RuntimeError(
                "ComfyUI modern video API is unavailable; cannot safely persist the final video."
            ) from exc

        width, height = video.get_dimensions()
        full_output_folder, filename, counter, subfolder, resolved_prefix = (
            folder_paths.get_save_image_path(
                str(filename_prefix or "video/meetmap_scail2_character_swap_v5"),
                folder_paths.get_output_directory(),
                width,
                height,
            )
        )
        Path(full_output_folder).mkdir(parents=True, exist_ok=True)

        attempts = [
            ("mp4", "h264", "veryfast"),
            ("mkv", "h264", "ultrafast"),
            ("mp4", "auto", None),
            ("webm", "av1", None),
        ]
        failures = []

        for index, (container_name, codec_name, preset) in enumerate(attempts, start=1):
            extension = Types.VideoContainer.get_extension(container_name)
            suffix = "" if index == 1 else f"_fallback{index}"
            file_name = f"{filename}_{counter:05}_{suffix}.{extension}"
            destination = os.path.join(full_output_folder, file_name)
            try:
                kwargs = {
                    "format": Types.VideoContainer(container_name),
                    "codec": Types.VideoCodec(codec_name),
                }
                if preset:
                    kwargs["preset"] = preset
                video.save_to(destination, **kwargs)

                if not os.path.isfile(destination) or os.path.getsize(destination) <= 0:
                    raise RuntimeError("encoder returned without creating a non-empty file")

                status = (
                    f"Final video saved successfully: {destination}. "
                    f"container={container_name}, codec={codec_name}, attempt={index}/{len(attempts)}."
                )
                if failures:
                    status += " Recovery from earlier save failures: " + " | ".join(failures)
                print("[MeetMap Output] " + status)
                return (video, status)
            except Exception as exc:
                try:
                    if os.path.isfile(destination):
                        os.remove(destination)
                except Exception:
                    pass
                failure = (
                    f"attempt {index} {container_name}/{codec_name}: "
                    f"{type(exc).__name__}: {exc}"
                )
                failures.append(failure)
                print("[MeetMap Output] WARNING: " + failure)

        raise RuntimeError(
            "Final video could not be saved using any supported container/codec fallback. "
            + " | ".join(failures)
        )


NODE_CLASS_MAPPINGS = {
    "MeetMapSafeSaveVideo": MeetMapSafeSaveVideo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeetMapSafeSaveVideo": "MeetMap Safe Save Video",
}
