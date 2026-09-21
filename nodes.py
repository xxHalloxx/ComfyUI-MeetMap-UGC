import gc
import json
import math
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import folder_paths
from huggingface_hub import hf_hub_download
from llama_cpp import Llama


DEFAULT_MODEL = "Qwen_Qwen3-4B-Instruct-2507-Q5_K_M.gguf"
DEFAULT_MODEL_REPO = "bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF"
REQUIRED_FIELDS = (
    "topic",
    "hook",
    "spoken_script_de",
    "image_prompt",
    "video_prompt",
    "duration_seconds",
)
BEAT_FIELDS = ("start", "end", "expression", "movement")


SYSTEM_PROMPT = r"""
Du erstellst genau ein authentisches deutsches Social-Media-UGC-Konzept für MeetMap.

MeetMap ist eine Social-Discovery-App für junge Erwachsene. Sie hilft Menschen, neue Leute und Freunde kennenzulernen, spontane Aktivitäten, Events und Meetups zu finden, Tennispartner und Running Groups zu entdecken, Coffee Meetups, Picnics, Book Clubs, Photo Walks und Language Exchanges zu organisieren, nach einem Umzug neue Menschen zu treffen und spontan rauszugehen statt allein zuhause zu sitzen.

Kerngefühl: echte Menschen, echte Aktivitäten, reale Treffen, Spontanität, die Stadt entdecken und weniger Einsamkeit.
Brand: jung, locker, menschlich, modern, authentisch, nicht corporate und keine klassische Werbung.

Antworte ausschließlich mit genau einem JSON-Objekt und exakt diesen Feldern:
{
  "topic": "...",
  "hook": "...",
  "spoken_script_de": "...",
  "image_prompt": "...",
  "video_prompt": "...",
  "duration_seconds": 12,
  "acting_beats": [
    {"start": 0.0, "end": 4.0, "expression": "relaxed and friendly", "movement": "small blink and minimal head movement"},
    {"start": 4.0, "end": 8.0, "expression": "slightly more animated", "movement": "tiny eyebrow raise and one restrained hand gesture"},
    {"start": 8.0, "end": 12.0, "expression": "calm and friendly", "movement": "small head tilt and steady eye contact"}
  ]
}

Regeln:
- spoken_script_de ist vollständig Deutsch, natürliche junge Alltagssprache, 22 bis 35 Wörter, genau ein Hauptgedanke, kein Werbesprecher, keine Marketing-Floskel und kein künstlicher CTA.
- duration_seconds ist eine ganze Zahl zwischen 10 und 15. Bei gültigem duration_override hat dieser Wert Vorrang; sonst anhand der Sprechlänge bestimmen.
- image_prompt ist Englisch und beschreibt standardmäßig eine junge erwachsene Frau zuhause im Schlafzimmer oder Wohnzimmer, sitzend auf Bett, Sofa oder Stuhl, Smartphone-Frontkamera auf Augenhöhe, direkter Blick in die Kamera, Alltagskleidung, glaubwürdige leicht unperfekte Wohnung und natürliche Raumbeleuchtung. Sichtbare feine Poren, Haut-Mikrotextur, kleine Unreinheiten, feine Gesichtshaare, natürliche Lippen, realistische Augen und Augenbrauen, leichte Gesichtsasymmetrie, dezentes Sensorrauschen, unpoliertes Smartphone-Foto, realistische Belichtung und Stofftextur. Kein Studio, Commercial, Fashion Shoot, Stockfoto, CGI, 3D-Render, Beauty-Filter, Airbrush, Plastik- oder Wachshaut und keine perfekte Symmetrie.
- video_prompt ist Englisch und zeigt dieselbe Person, Kleidung, Frisur, Beleuchtung und denselben Raum. Realistisches vertikales Smartphone-UGC, direkte Ansprache, natürliche Mikro-Bewegungen, stabile Identität und stabiler Hintergrund. Keine Kamerafahrt, kein dramatischer Zoom, kein Morphing und keine übertriebene Gestik.
- image_prompt und video_prompt müssen dieselbe Person und Situation beschreiben.
- acting_beats enthält 3 oder höchstens 4 zeitlich lückenlose Beats, die bei 0.0 beginnen und exakt bei duration_seconds enden. Keine Überschneidungen und keine dramatischen Bewegungen.
""".strip()


def _available_models():
    model_dir = os.path.join(folder_paths.models_dir, "LLM")
    try:
        names = sorted(
            name
            for name in os.listdir(model_dir)
            if os.path.isfile(os.path.join(model_dir, name)) and name.lower().endswith(".gguf")
        )
    except FileNotFoundError:
        names = []
    if DEFAULT_MODEL not in names:
        names.insert(0, DEFAULT_MODEL)
    return names


def _llm_dir() -> Path:
    return Path(folder_paths.models_dir) / "LLM"


def _ensure_default_model(model_name: str) -> Path:
    """Download only the documented default GGUF when it is absent locally."""
    target_dir = _llm_dir()
    target = target_dir / model_name
    if target.is_file():
        return target
    if model_name != DEFAULT_MODEL:
        raise FileNotFoundError(f"Selected GGUF model not found: {target}")

    target_dir.mkdir(parents=True, exist_ok=True)
    print(f"[MeetMap UGC] Downloading missing Qwen GGUF to {target}")
    downloaded = Path(
        hf_hub_download(
            repo_id=DEFAULT_MODEL_REPO,
            filename=DEFAULT_MODEL,
            local_dir=str(target_dir),
        )
    )
    if not downloaded.is_file():
        raise RuntimeError(f"Qwen download did not create the expected file: {downloaded}")
    return downloaded


def _extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.I | re.S)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", cleaned):
        try:
            value, _ = decoder.raw_decode(cleaned[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("The LLM response did not contain a complete JSON object.")


def _parse_override(value: str):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if 10 <= number <= 15 else None


def _validate_acting_beats(value: Any, duration: int) -> str:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise ValueError("acting_beats must contain one to four beats.")
    beats = []
    for beat in value:
        if not isinstance(beat, dict) or any(field not in beat for field in BEAT_FIELDS):
            raise ValueError("Every acting beat needs start, end, expression and movement.")
        try:
            start = float(beat["start"])
            end = float(beat["end"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Acting beat times must be numeric.") from exc
        if not isinstance(beat["expression"], str) or not isinstance(beat["movement"], str):
            raise ValueError("Acting beat expression and movement must be strings.")
        beats.append({"start": start, "end": end, "expression": beat["expression"].strip(), "movement": beat["movement"].strip()})
    beats.sort(key=lambda item: item["start"])
    tolerance = 0.06
    if abs(beats[0]["start"]) > tolerance or abs(beats[-1]["end"] - duration) > tolerance:
        raise ValueError("Acting beats must cover the complete duration.")
    beats[0]["start"] = 0.0
    beats[-1]["end"] = float(duration)
    for previous, current in zip(beats, beats[1:]):
        if abs(previous["end"] - current["start"]) > tolerance or current["start"] < previous["end"] - tolerance:
            raise ValueError("Acting beats must be contiguous and non-overlapping.")
        current["start"] = previous["end"]
    return json.dumps(beats, ensure_ascii=False, separators=(",", ":"))


def _validate_payload(payload: Dict[str, Any], duration_override: str) -> Dict[str, Any]:
    missing = [key for key in REQUIRED_FIELDS if key not in payload]
    if missing:
        raise ValueError(f"Missing required JSON fields: {', '.join(missing)}")

    normalized = {}
    for key in REQUIRED_FIELDS[:-1]:
        value = payload[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Field '{key}' must be a non-empty string.")
        normalized[key] = value.strip()

    override = _parse_override(duration_override)
    if override is not None:
        duration = override
    else:
        try:
            duration = int(round(float(payload["duration_seconds"])))
        except (TypeError, ValueError):
            words = len(re.findall(r"\b\w+[\w'-]*\b", normalized["spoken_script_de"], flags=re.UNICODE))
            duration = int(round(words / 2.35 + 0.8))
        duration = max(10, min(15, duration))
    normalized["duration_seconds"] = duration
    normalized["acting_beats"] = _validate_acting_beats(payload.get("acting_beats"), duration)
    return normalized


class MeetMapVideoBatchPlanner:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_count": ("INT", {"default": 1, "min": 1, "max": 10, "step": 1}),
                "base_seed": (
                    "INT",
                    {
                        "default": 42,
                        "min": 0,
                        "max": 0xFFFFFFFFFFFFFFFF,
                    },
                ),
                "seed_mode": (["increment", "random_per_video"], {"default": "increment"}),
                "save_run_folder": ("BOOLEAN", {"default": True}),
                "filename_root": (
                    "STRING",
                    {"default": "video/meetmap_ugc_v2", "multiline": False},
                ),
            }
        }

    RETURN_TYPES = (
        "STRING",
        "INT",
        "INT",
        "INT",
        "INT",
        "INT",
        "INT",
        "STRING",
        "STRING",
    )
    RETURN_NAMES = (
        "variation_note",
        "video_index",
        "total_videos",
        "content_seed",
        "flux_seed",
        "face_seed",
        "ltx_seed",
        "filename_prefix",
        "progress",
    )
    OUTPUT_IS_LIST = (True, True, True, True, True, True, True, True, True)
    FUNCTION = "plan"
    CATEGORY = "MeetMap/UGC"
    DESCRIPTION = (
        "Creates one mapped ComfyUI job per requested video. Downstream nodes automatically "
        "execute once per list item, so the full MeetMap pipeline is regenerated for every video."
    )

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def plan(self, video_count, base_seed, seed_mode, save_run_folder, filename_root):
        count = max(1, min(10, int(video_count)))
        base = int(base_seed) & 0xFFFFFFFFFFFFFFFF
        root = str(filename_root).strip().strip("/") or "video/meetmap_ugc_v2"

        if str(seed_mode) == "random_per_video":
            rng = random.Random(base)
            content_seeds = []
            seen = set()
            while len(content_seeds) < count:
                value = rng.randrange(0, 0x10000000000000000)
                if value not in seen:
                    seen.add(value)
                    content_seeds.append(value)
        else:
            content_seeds = [
                (base + index) & 0xFFFFFFFFFFFFFFFF
                for index in range(count)
            ]

        run_stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_folder = f"run_{run_stamp}_{base & 0xFFFF:04x}"

        variation_notes = []
        video_indices = []
        totals = []
        flux_seeds = []
        face_seeds = []
        ltx_seeds = []
        filename_prefixes = []
        progress_labels = []

        for zero_index, seed in enumerate(content_seeds):
            index = zero_index + 1
            variation_notes.append(
                f"Variation {index} of {count}. Create a clearly distinct MeetMap concept, hook, "
                "German spoken script, image prompt and video prompt for this variation. Avoid "
                "reusing the same angle, opening sentence, activity, setting details or wording "
                "from the other variations in this batch. Keep it authentic young smartphone UGC."
            )
            video_indices.append(index)
            totals.append(count)
            flux_seeds.append((seed + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF)
            face_seeds.append((seed + 0xD1B54A32D192ED03) & 0xFFFFFFFFFFFFFFFF)
            ltx_seeds.append((seed + 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF)
            if bool(save_run_folder):
                filename_prefixes.append(f"{root}/{run_folder}/video_{index:02d}")
            else:
                filename_prefixes.append(f"{root}/video_{index:02d}")
            progress_labels.append(f"Video {index} / {count}")

        return (
            variation_notes,
            video_indices,
            totals,
            content_seeds,
            flux_seeds,
            face_seeds,
            ltx_seeds,
            filename_prefixes,
            progress_labels,
        )


class MeetMapContentGenerator:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model_name": (_available_models(), {"default": DEFAULT_MODEL}),
                "optional_topic": ("STRING", {"default": "AUTO", "multiline": False}),
                "person_presentation": ("STRING", {"default": "AUTO", "multiline": False}),
                "room_type": ("STRING", {"default": "AUTO", "multiline": False}),
                "duration_override": ("STRING", {"default": "AUTO", "multiline": False}),
                "content_style": ("STRING", {"default": "casual_ugc", "multiline": False}),
                "seed": (
                    "INT",
                    {
                        "default": 1,
                        "min": 0,
                        "max": 0xFFFFFFFFFFFFFFFF,
                        "control_after_generate": True,
                    },
                ),
            },
            "optional": {
                "variation_note": ("STRING", {"forceInput": True, "multiline": True}),
                "video_index": ("INT", {"forceInput": True, "min": 1, "max": 10}),
                "total_videos": ("INT", {"forceInput": True, "min": 1, "max": 10}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "INT", "STRING")
    RETURN_NAMES = ("topic", "hook", "spoken_script_de", "image_prompt", "video_prompt", "duration_seconds", "acting_beats")
    FUNCTION = "generate"
    CATEGORY = "MeetMap/UGC"
    DESCRIPTION = "Runs one local CPU-only Qwen GGUF inference and returns validated MeetMap UGC fields."

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def generate(
        self,
        model_name,
        optional_topic,
        person_presentation,
        room_type,
        duration_override,
        content_style,
        seed,
        variation_note="",
        video_index=1,
        total_videos=1,
    ):
        model_path = os.path.abspath(os.path.join(folder_paths.models_dir, "LLM", model_name))
        allowed_root = os.path.abspath(os.path.join(folder_paths.models_dir, "LLM")) + os.sep
        if not model_path.startswith(allowed_root):
            raise ValueError("model_name must resolve inside ComfyUI/models/LLM/.")
        model_path = str(_ensure_default_model(model_name))

        user_prompt = (
            "Erzeuge jetzt genau ein Konzept mit diesen Eingaben:\n"
            f"optional_topic={optional_topic}\n"
            f"person_presentation={person_presentation}\n"
            f"room_type={room_type}\n"
            f"duration_override={duration_override}\n"
            f"content_style={content_style}\n"
            f"video_index={int(video_index)}\n"
            f"total_videos={int(total_videos)}\n"
            f"variation_note={str(variation_note).strip() or 'single-video run'}\n"
            "Wenn total_videos > 1 ist, muss diese Variante in Thema, Hook, Formulierung, Aktivitaet und "
            "visueller Situation klar eigenstaendig sein. Vermeide Wiederholungen innerhalb des Batches.\n"
            "AUTO bedeutet: selbst sinnvoll und abwechslungsreich entscheiden. Nur das JSON-Objekt ausgeben."
        )

        llama = None
        try:
            llama = Llama(
                model_path=model_path,
                n_ctx=8192,
                n_gpu_layers=0,
                seed=int(seed),
                verbose=False,
            )
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
            parse_error = None
            for attempt in range(2):
                if attempt == 1:
                    messages.append(
                        {
                            "role": "user",
                            "content": "Die vorige Antwort war technisch nicht als vollständiges Pflichtfeld-JSON parsebar. Gib jetzt ausschließlich das geforderte vollständige JSON-Objekt aus.",
                        }
                    )
                response = llama.create_chat_completion(
                    messages=messages,
                    temperature=0.85 if attempt == 0 else 0.2,
                    top_p=0.9,
                    max_tokens=1400,
                    seed=int(seed) + attempt,
                )
                text = response["choices"][0]["message"]["content"]
                try:
                    payload = _validate_payload(_extract_json_object(text), duration_override)
                    return tuple(payload[key] for key in REQUIRED_FIELDS) + (payload["acting_beats"],)
                except (ValueError, TypeError, KeyError) as exc:
                    parse_error = exc
            raise ValueError(f"Qwen returned invalid MeetMap JSON after one retry: {parse_error}")
        finally:
            if llama is not None:
                close = getattr(llama, "close", None)
                if callable(close):
                    close()
            del llama
            gc.collect()


class MeetMapLTXPromptBuilder:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_prompt": ("STRING", {"multiline": True, "forceInput": True}),
                "spoken_script_de": ("STRING", {"multiline": True, "forceInput": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "build"
    CATEGORY = "MeetMap/UGC"

    def build(self, video_prompt, spoken_script_de):
        spoken = str(spoken_script_de).strip().replace('"', '\\"')
        prompt = f'''{str(video_prompt).strip()}

This is a realistic vertical smartphone UGC recording. The person looks directly into the smartphone camera and speaks German naturally in a casual young voice.

The person says exactly:
"{spoken}"

The quoted German sentence must be spoken exactly as written. Do not translate it, rewrite it, add words, or omit words.

Natural synchronized speech and mouth motion. Subtle blinking, breathing, small head movements, small shoulder movements and occasional restrained natural hand gestures. Preserve the exact facial identity, hairstyle, clothing, body proportions, lighting and room throughout the clip. Natural skin texture remains visible. Stable smartphone UGC framing.

No cinematic camera movement. No dramatic zoom. No morphing. No identity drift. No face changes. No clothing changes. No background changes. No exaggerated gestures. No beauty-filter appearance.'''
        return (prompt,)


class MeetMapLTXRelayPromptBuilder:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_prompt": ("STRING", {"multiline": True, "forceInput": True}),
                "spoken_script_de": ("STRING", {"multiline": True, "forceInput": True}),
                "acting_beats": ("STRING", {"multiline": True, "forceInput": True}),
                "duration_seconds": ("INT", {"forceInput": True, "min": 10, "max": 15}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "build"
    CATEGORY = "MeetMap/UGC"

    def build(self, video_prompt, spoken_script_de, acting_beats, duration_seconds):
        spoken = str(spoken_script_de).strip().replace('"', '\\"')
        try:
            beats = json.loads(acting_beats)
        except (TypeError, json.JSONDecodeError):
            beats = []
        lines = []
        for beat in beats:
            lines.append(
                f"[{float(beat['start']):.1f}s-{float(beat['end']):.1f}s]\n"
                f"Expression: {beat['expression']}. Movement: {beat['movement']}."
            )
        relay = "\n\n".join(lines) or "[0.0s-{:.1f}s]\nRelaxed direct eye contact, subtle blinking and minimal natural head movement.".format(float(duration_seconds))
        prompt = f'''{str(video_prompt).strip()}

This is a realistic vertical smartphone selfie video, not a cinematic commercial. Use a front camera, casual phone stabilization, slight handheld micro movement, minor autofocus breathing and minor exposure variation. Keep the framing natural and the background stable.

The person says exactly:
"{spoken}"

The quoted German sentence must be spoken exactly as written. Do not translate, rewrite, add words, or omit words. Use natural German speech, synchronized mouth motion and normal conversational pacing.

ACTING RELAY FOR THE COMPLETE {int(duration_seconds)} SECOND CLIP:
{relay}

Maintain direct eye contact most of the time, natural blinking, subtle breathing, restrained shoulder movement and at most one small hand gesture per beat. Preserve stable identity, skin texture, hair, clothing and room. No morphing, identity drift, face changes, clothing changes, background changes, exaggerated gestures, orbit, dolly, dramatic zoom or cinematic camera movement.'''
        return (prompt,)


NODE_CLASS_MAPPINGS = {
    "MeetMapVideoBatchPlanner": MeetMapVideoBatchPlanner,
    "MeetMapContentGenerator": MeetMapContentGenerator,
    "MeetMapLTXPromptBuilder": MeetMapLTXPromptBuilder,
    "MeetMapLTXRelayPromptBuilder": MeetMapLTXRelayPromptBuilder,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeetMapVideoBatchPlanner": "MeetMap Video Batch Planner",
    "MeetMapContentGenerator": "MeetMap Content Generator",
    "MeetMapLTXPromptBuilder": "MeetMap LTX Prompt Builder",
    "MeetMapLTXRelayPromptBuilder": "MeetMap LTX Relay Prompt Builder",
}
