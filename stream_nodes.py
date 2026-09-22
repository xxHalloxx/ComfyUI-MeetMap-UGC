import io
import math
import os
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from PIL import Image

import folder_paths


def _safe_video_source_path(video, workdir: Path) -> Path:
    source = video.get_stream_source()
    if isinstance(source, str):
        return Path(source).resolve()
    if isinstance(source, Path):
        return source.resolve()
    if isinstance(source, io.BytesIO):
        target = workdir / "source_materialized.mp4"
        source.seek(0)
        target.write_bytes(source.read())
        return target
    # Last-resort materialization through ComfyUI.
    from comfy_api.latest import Types
    target = workdir / "source_materialized.mp4"
    video.save_to(
        str(target),
        format=Types.VideoContainer.MP4,
        codec=Types.VideoCodec.H264,
        preset="ultrafast",
    )
    return target


def _silence(duration: float, sample_rate: int = 44100):
    samples = max(1, int(round(max(0.01, float(duration)) * sample_rate)))
    return {
        "waveform": torch.zeros((1, 1, samples), dtype=torch.float32),
        "sample_rate": sample_rate,
    }


def _extract_audio(video, duration: float, workdir: Path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return _silence(duration), "WARNING: ffmpeg unavailable; source audio replaced with silence."

    try:
        source = _safe_video_source_path(video, workdir)
        start_time, active_duration = video.get_active_trim_window()
        target_duration = min(float(duration), float(active_duration)) if active_duration else float(duration)
        wav = workdir / "source_audio.wav"
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
        ]
        if float(start_time) > 0:
            command += ["-ss", f"{float(start_time):.6f}"]
        command += [
            "-i",
            str(source),
            "-vn",
            "-t",
            f"{max(0.01, target_duration):.6f}",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-c:a",
            "pcm_f32le",
            str(wav),
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(60, int(target_duration * 4) + 30),
        )
        if result.returncode != 0 or not wav.is_file() or wav.stat().st_size <= 44:
            raise RuntimeError(result.stderr.strip() or f"ffmpeg exit={result.returncode}")

        samples, sample_rate = sf.read(str(wav), dtype="float32", always_2d=True)
        waveform = torch.from_numpy(samples.T.copy()).unsqueeze(0)
        return {
            "waveform": waveform,
            "sample_rate": int(sample_rate),
        }, "source audio extracted by ffmpeg without full-frame video decoding"
    except Exception as exc:
        return _silence(duration), (
            "WARNING: source audio extraction failed; silence fallback used. "
            f"{type(exc).__name__}: {exc}"
        )


class MeetMapSourceStreamPrepare:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video": ("VIDEO",),
                "crop_x": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 1}),
                "crop_y": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 1}),
                "crop_width": ("INT", {"default": 512, "min": 32, "max": 16384, "step": 32}),
                "crop_height": ("INT", {"default": 960, "min": 32, "max": 16384, "step": 32}),
                "frame_count": ("INT", {"default": 2401, "min": 1, "max": 2401, "step": 4}),
                "feather_pixels": ("INT", {"default": 32, "min": 0, "max": 256, "step": 1}),
            }
        }

    RETURN_TYPES = (
        "VIDEO",
        "VIDEO",
        "IMAGE",
        "AUDIO",
        "FLOAT",
        "INT",
        "INT",
        "INT",
        "INT",
        "INT",
        "FLOAT",
        "INT",
        "STRING",
    )
    RETURN_NAMES = (
        "pose_video",
        "source_video",
        "first_frame",
        "audio",
        "fps",
        "crop_x",
        "crop_y",
        "crop_width",
        "crop_height",
        "frame_count",
        "duration_seconds",
        "feather_pixels",
        "status",
    )
    FUNCTION = "prepare"
    CATEGORY = "MeetMap/Runtime"
    DESCRIPTION = (
        "Streaming source preparation. Uses video metadata for size/FPS/frame count, "
        "keeps source and cropped driving video file-backed, and decodes only one first "
        "frame. This avoids GetVideoComponents materializing the entire source video."
    )

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def prepare(
        self,
        video,
        crop_x,
        crop_y,
        crop_width,
        crop_height,
        frame_count,
        feather_pixels,
    ):
        width_raw, height_raw = video.get_dimensions()
        source_width = int(width_raw)
        source_height = int(height_raw)
        if source_width < 32 or source_height < 32:
            raise RuntimeError(
                f"Source video is too small for SCAIL: {source_width}x{source_height}."
            )

        fps = float(video.get_frame_rate())
        if not math.isfinite(fps) or fps <= 0:
            raise RuntimeError(f"Invalid source FPS metadata: {fps}")

        try:
            available = max(1, int(video.get_frame_count()))
        except Exception:
            duration_meta = max(0.01, float(video.get_duration()))
            available = max(1, int(round(duration_meta * fps)))

        requested = max(1, min(int(frame_count), available))
        if requested > 1:
            requested = ((requested - 1) // 4) * 4 + 1
        requested = max(1, min(requested, available))
        duration = float(requested) / fps

        def snap32(value, maximum):
            maximum32 = (int(maximum) // 32) * 32
            if maximum32 < 32:
                raise RuntimeError("Source dimension cannot provide a 32px-aligned crop.")
            value = max(32, min(int(value), maximum32))
            return max(32, (value // 32) * 32)

        crop_w = snap32(crop_width, source_width)
        crop_h = snap32(crop_height, source_height)
        x = max(0, min(int(crop_x), source_width - crop_w))
        y = max(0, min(int(crop_y), source_height - crop_h))
        feather = min(max(0, int(feather_pixels)), crop_w // 2, crop_h // 2)

        trimmed = video.as_trimmed(
            start_time=0.0,
            duration=duration,
            strict_duration=False,
        )
        if trimmed is None:
            trimmed = video
        pose_video = trimmed.as_cropped(x=x, y=y, width=crop_w, height=crop_h)

        # Decode exactly one cropped frame. Never materialize the complete video here.
        first_slice = pose_video.as_trimmed(
            start_time=0.0,
            duration=max(1.0 / fps, 0.001),
            strict_duration=False,
        )
        if first_slice is None:
            first_slice = pose_video
        components = first_slice.get_components()
        if components.images is None or components.images.shape[0] < 1:
            raise RuntimeError("Could not decode the first source frame.")
        first_frame = components.images[:1, ..., :3].contiguous()

        workdir = Path(folder_paths.get_temp_directory()) / "meetmap_stream"
        workdir.mkdir(parents=True, exist_ok=True)
        audio, audio_status = _extract_audio(trimmed, duration, workdir)

        status = (
            f"STREAMING source ready: source={source_width}x{source_height}, "
            f"crop={crop_w}x{crop_h}@({x},{y}), {requested}/{available} frames, "
            f"{fps:.3f} fps, {duration:.3f}s. Full video was NOT materialized. "
            f"Only one cropped first frame was decoded; {audio_status}."
        )
        print("[MeetMap Stream] " + status)

        return (
            pose_video,
            trimmed,
            first_frame,
            audio,
            fps,
            x,
            y,
            crop_w,
            crop_h,
            requested,
            duration,
            feather,
            status,
        )


def _audio_to_wav(audio, destination: Path):
    if not isinstance(audio, dict):
        raise RuntimeError("Audio payload is not a ComfyUI AUDIO dict.")
    waveform = audio.get("waveform")
    sample_rate = int(audio.get("sample_rate") or 44100)
    if not isinstance(waveform, torch.Tensor) or waveform.numel() == 0:
        raise RuntimeError("Audio waveform is empty.")
    wave = waveform.detach().cpu().float()
    if wave.ndim == 3:
        wave = wave[0]
    elif wave.ndim == 1:
        wave = wave.unsqueeze(0)
    elif wave.ndim > 3:
        wave = wave.reshape(-1, wave.shape[-1])[:2]
    array = wave.numpy().T
    sf.write(str(destination), array, sample_rate, subtype="PCM_16")
    if not destination.is_file() or destination.stat().st_size <= 44:
        raise RuntimeError("Audio WAV write failed.")


def _make_feather_mask(width: int, height: int, feather: int, destination: Path):
    if feather <= 0:
        mask = np.full((height, width), 255, dtype=np.uint8)
    else:
        xx = np.minimum(np.arange(width), np.arange(width)[::-1]).astype(np.float32)
        yy = np.minimum(np.arange(height), np.arange(height)[::-1]).astype(np.float32)
        dist = np.minimum(yy[:, None], xx[None, :])
        alpha = np.clip(dist / max(1.0, float(feather)), 0.0, 1.0)
        mask = np.round(alpha * 255.0).astype(np.uint8)
    Image.fromarray(mask, mode="L").save(destination)


class MeetMapStreamCompositeVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source_video": ("VIDEO",),
                "replacement_images": ("IMAGE",),
                "audio": ("AUDIO",),
                "fps": ("FLOAT", {"forceInput": True, "min": 0.01, "max": 240.0}),
                "crop_x": ("INT", {"forceInput": True, "min": 0, "max": 16384}),
                "crop_y": ("INT", {"forceInput": True, "min": 0, "max": 16384}),
                "crop_width": ("INT", {"forceInput": True, "min": 32, "max": 16384}),
                "crop_height": ("INT", {"forceInput": True, "min": 32, "max": 16384}),
                "feather_pixels": ("INT", {"forceInput": True, "min": 0, "max": 256}),
            }
        }

    RETURN_TYPES = ("VIDEO", "STRING")
    RETURN_NAMES = ("video", "status")
    FUNCTION = "composite"
    CATEGORY = "MeetMap/Runtime"
    DESCRIPTION = (
        "Streaming final compositor. Encodes only the generated crop, then overlays it on "
        "the file-backed source via ffmpeg. Full-resolution source frames never become one "
        "giant IMAGE tensor. If overlay fails, returns a crop-only video instead of losing the run."
    )

    def composite(
        self,
        source_video,
        replacement_images,
        audio,
        fps,
        crop_x,
        crop_y,
        crop_width,
        crop_height,
        feather_pixels,
    ):
        from comfy_api.latest import InputImpl, Types

        if not isinstance(replacement_images, torch.Tensor) or replacement_images.ndim != 4:
            raise RuntimeError("replacement_images must be an IMAGE batch.")

        fps_value = float(fps)
        frame_count = int(replacement_images.shape[0])
        if frame_count < 1:
            raise RuntimeError("No generated replacement frames are available.")
        duration = frame_count / fps_value

        temp_root = Path(folder_paths.get_temp_directory()) / "meetmap_stream"
        temp_root.mkdir(parents=True, exist_ok=True)
        job = Path(tempfile.mkdtemp(prefix="composite_", dir=str(temp_root)))

        overlay_path = job / "replacement.mp4"
        audio_path = job / "final_audio.wav"
        mask_path = job / "feather_mask.png"
        output_path = job / "composited.mp4"

        # Crop-only video is also our final fallback.
        overlay_video = InputImpl.VideoFromComponents(
            Types.VideoComponents(
                images=replacement_images[..., :3].clamp(0.0, 1.0),
                audio=audio,
                frame_rate=Fraction(fps_value).limit_denominator(100000),
            )
        )
        try:
            overlay_video.save_to(
                str(overlay_path),
                format=Types.VideoContainer.MP4,
                codec=Types.VideoCodec.H264,
                crf=18,
                preset="ultrafast",
                color_space="sRGB",
            )
        except Exception as exc:
            raise RuntimeError(f"Could not encode generated crop for streaming composite: {exc}") from exc

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            status = (
                "WARNING: ffmpeg unavailable for full-frame overlay; returning generated crop-only video."
            )
            print("[MeetMap Stream] " + status)
            return (InputImpl.VideoFromFile(str(overlay_path)), status)

        try:
            source_path = _safe_video_source_path(source_video, job)
            start_time, active_duration = source_video.get_active_trim_window()
            _audio_to_wav(audio, audio_path)
            _make_feather_mask(
                int(crop_width),
                int(crop_height),
                int(feather_pixels),
                mask_path,
            )

            command = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
            ]
            if float(start_time) > 0:
                command += ["-ss", f"{float(start_time):.6f}"]
            command += [
                "-t",
                f"{duration:.6f}",
                "-i",
                str(source_path),
                "-i",
                str(overlay_path),
                "-loop",
                "1",
                "-framerate",
                f"{fps_value:.6f}",
                "-i",
                str(mask_path),
                "-i",
                str(audio_path),
                "-filter_complex",
                (
                    f"[1:v]scale={int(crop_width)}:{int(crop_height)},format=rgba[fg];"
                    f"[2:v]format=gray[mask];"
                    f"[fg][mask]alphamerge[fgm];"
                    f"[0:v][fgm]overlay={int(crop_x)}:{int(crop_y)}:"
                    "shortest=1:format=auto[v]"
                ),
                "-map",
                "[v]",
                "-map",
                "3:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-shortest",
                str(output_path),
            ]
            timeout = max(120, int(duration * 12) + 60)
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
                raise RuntimeError(result.stderr.strip() or f"ffmpeg exit={result.returncode}")

            status = (
                f"STREAMING composite succeeded: {frame_count} generated crop frames over "
                f"file-backed source at ({int(crop_x)},{int(crop_y)}), "
                f"{int(feather_pixels)}px feather. Full source was never materialized."
            )
            print("[MeetMap Stream] " + status)
            return (InputImpl.VideoFromFile(str(output_path)), status)
        except Exception as exc:
            status = (
                "WARNING: full-frame streaming composite failed; generated crop-only video "
                f"returned so the run can still finish. {type(exc).__name__}: {exc}"
            )
            print("[MeetMap Stream] " + status)
            return (InputImpl.VideoFromFile(str(overlay_path)), status)


NODE_CLASS_MAPPINGS = {
    "MeetMapSourceStreamPrepare": MeetMapSourceStreamPrepare,
    "MeetMapStreamCompositeVideo": MeetMapStreamCompositeVideo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeetMapSourceStreamPrepare": "MeetMap Source Stream Prepare",
    "MeetMapStreamCompositeVideo": "MeetMap Stream Composite Video",
}
