import gc
import json
import os
import re
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
  "duration_seconds": 12
}

Regeln:
- spoken_script_de ist vollständig Deutsch, natürliche junge Alltagssprache, 22 bis 35 Wörter, genau ein Hauptgedanke, kein Werbesprecher, keine Marketing-Floskel und kein künstlicher CTA.
- duration_seconds ist eine ganze Zahl zwischen 10 und 15. Bei gültigem duration_override hat dieser Wert Vorrang; sonst anhand der Sprechlänge bestimmen.
- image_prompt ist Englisch und beschreibt exakt eine junge erwachsene Person zuhause im Schlafzimmer oder Wohnzimmer, sitzend auf Bett, Sofa oder Stuhl, Smartphone-Frontkamera auf Augenhöhe, direkter Blick in die Kamera, Alltagskleidung, glaubwürdige leicht unperfekte Wohnung und natürliche Raumbeleuchtung. Sichtbare feine Poren, Haut-Mikrotextur, kleine Unreinheiten, feine Gesichtshaare, natürliche Lippen, realistische Augen und Augenbrauen, leichte Gesichtsasymmetrie, dezentes Sensorrauschen, unpoliertes Smartphone-Foto, realistische Belichtung und Stofftextur. Kein Studio, Commercial, Fashion Shoot, Stockfoto, CGI, 3D-Render, Beauty-Filter, Airbrush, Plastik- oder Wachshaut und keine perfekte Symmetrie.
- video_prompt ist Englisch und zeigt dieselbe Person, Kleidung, Frisur, Beleuchtung und denselben Raum. Realistisches vertikales Smartphone-UGC, direkte Ansprache, natürliche Mikro-Bewegungen, stabile Identität und stabiler Hintergrund. Keine Kamerafahrt, kein dramatischer Zoom, kein Morphing und keine übertriebene Gestik.
- image_prompt und video_prompt müssen dieselbe Person und Situation beschreiben.
""".strip()


def _llm_dir() -> str:
    return os.path.abspath(os.path.join(folder_paths.models_dir, "LLM"))


def _available_models():
    model_dir = _llm_dir()
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


def _safe_model_path(model_name: str) -> str:
    model_dir = _llm_dir()
    model_path = os.path.abspath(os.path.join(model_dir, model_name))
    if os.path.commonpath([model_dir, model_path]) != model_dir:
        raise ValueError("model_name must resolve inside ComfyUI/models/LLM/.")
    return model_path


def _ensure_default_model(model_name: str) -> str:
    model_path = _safe_model_path(model_name)
    if os.path.isfile(model_path) and os.path.getsize(model_path) > 0:
        return model_path

    if model_name != DEFAULT_MODEL:
        raise FileNotFoundError(f"GGUF model not found: {model_path}")

    os.makedirs(_llm_dir(), exist_ok=True)
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    print(f"[MeetMap UGC] Downloading missing Qwen model: {DEFAULT_MODEL}")
    hf_hub_download(
        repo_id=DEFAULT_MODEL_REPO,
        filename=DEFAULT_MODEL,
        local_dir=_llm_dir(),
    )

    if not os.path.isfile(model_path) or os.path.getsize(model_path) <= 0:
        raise FileNotFoundError(f"Qwen download finished but expected file is missing: {model_path}")
    return model_path


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
    return normalized


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
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "INT")
    RETURN_NAMES = ("topic", "hook", "spoken_script_de", "image_prompt", "video_prompt", "duration_seconds")
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
    ):
        model_path = _ensure_default_model(str(model_name))

        user_prompt = (
            "Erzeuge jetzt genau ein Konzept mit diesen Eingaben:\n"
            f"optional_topic={optional_topic}\n"
            f"person_presentation={person_presentation}\n"
            f"room_type={room_type}\n"
            f"duration_override={duration_override}\n"
            f"content_style={content_style}\n"
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
                            "content": (
                                "Die vorige Antwort war technisch nicht als vollständiges Pflichtfeld-JSON parsebar. "
                                "Gib jetzt ausschließlich das geforderte vollständige JSON-Objekt aus."
                            ),
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
                    return tuple(payload[key] for key in REQUIRED_FIELDS)
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
        spoken = str(spoken_script_de).strip()
        quoted_spoken = json.dumps(spoken, ensure_ascii=False)
        prompt = f"""{str(video_prompt).strip()}

This is a realistic vertical smartphone UGC recording. The person looks directly into the smartphone camera and speaks German naturally in a casual young voice.

The person says exactly:
{quoted_spoken}

The quoted German sentence must be spoken exactly as written. Do not translate it, rewrite it, add words, or omit words.

Natural synchronized speech and mouth motion. Subtle blinking, breathing, small head movements, small shoulder movements and occasional restrained natural hand gestures. Preserve the exact facial identity, hairstyle, clothing, body proportions, lighting and room throughout the clip. Natural skin texture remains visible. Stable smartphone UGC framing.

No cinematic camera movement. No dramatic zoom. No morphing. No identity drift. No face changes. No clothing changes. No background changes. No exaggerated gestures. No beauty-filter appearance."""
        return (prompt,)


NODE_CLASS_MAPPINGS = {
    "MeetMapContentGenerator": MeetMapContentGenerator,
    "MeetMapLTXPromptBuilder": MeetMapLTXPromptBuilder,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MeetMapContentGenerator": "MeetMap Content Generator",
    "MeetMapLTXPromptBuilder": "MeetMap LTX Prompt Builder",
}
