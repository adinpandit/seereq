"""Sarvam AI integration: speech-to-text, requirement analysis and document generation."""

import datetime
import json
import os
import re

import httpx
from sarvamai import SarvamAI
from sarvamai.core.api_error import ApiError

from . import prompts

# sarvam-105b reasons at length and often exhausts max_tokens on these prompts;
# the conversations variant answers directly and is ~30x faster here.
CHAT_MODEL = "sarvam-105b-conversations"
STT_MODEL = "saaras:v3"
TIMEOUT_SECONDS = 180

LANGUAGES = {"hinglish", "hindi", "english"}
LANGUAGE_LABELS = {"hinglish": "Hinglish", "hindi": "Hindi", "english": "English"}


class KiviError(Exception):
    """An error whose message is safe to show to the user."""

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


_client = None


def is_configured():
    return bool(os.environ.get("SARVAM_API_KEY"))


def client():
    global _client
    if not is_configured():
        raise KiviError("Sarvam AI is not configured. Add SARVAM_API_KEY to .env and restart the server.", 503)
    if _client is None:
        _client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"], timeout=TIMEOUT_SECONDS)
    return _client


def _call(fn, **kwargs):
    """Call the Sarvam SDK and translate failures into user-safe messages."""
    try:
        return fn(**kwargs)
    except ApiError as e:
        code = e.status_code
        if code in (401, 403):
            raise KiviError("Sarvam AI rejected the API key. Check SARVAM_API_KEY in .env.", 502)
        if code == 402:
            raise KiviError("Your Sarvam AI account has no credits left. Add credits in the Sarvam dashboard.", 502)
        if code == 429:
            raise KiviError("Sarvam AI rate limit reached. Wait a moment and try again.", 503)
        if code and 400 <= code < 500:
            raise KiviError(f"Sarvam AI could not process the request (HTTP {code}).", 502)
        raise KiviError("Sarvam AI is temporarily unavailable. Please try again.", 503)
    except httpx.TimeoutException:
        raise KiviError("Sarvam AI took too long to respond. Please try again.", 504)
    except httpx.HTTPError:
        raise KiviError("Could not reach Sarvam AI. Check your internet connection.", 503)


# ---------------------------------------------------------------- speech

def transcribe(audio, mime, language):
    codec = "webm"
    for key, value in (("mp4", "mp4"), ("ogg", "ogg"), ("wav", "wav"), ("mpeg", "mp3")):
        if key in mime:
            codec = value
    result = _call(
        client().speech_to_text.transcribe,
        file=(f"recording.{codec}", audio, mime or "audio/webm"),
        model=STT_MODEL,
        mode="transcribe" if language == "english" else "codemix",
        language_code="en-IN" if language == "english" else "hi-IN",
    )
    return (result.transcript or "").strip()


# ---------------------------------------------------------------- analysis

def _parse_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                pass
    return None


def _str_list(value, limit=12):
    if not isinstance(value, list):
        return []
    items = [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
    return items[:limit]


def _text(value, fallback=""):
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def normalize_analysis(data):
    """Validate a model (or client-supplied) analysis object into a known shape."""
    if not isinstance(data, dict):
        return None
    questions = []
    for q in data.get("clarification_questions") or []:
        if isinstance(q, str) and q.strip():
            q = {"question": q, "question_en": q, "topic": ""}
        if isinstance(q, dict) and _text(q.get("question")):
            questions.append({
                "question": _text(q.get("question")),
                "question_en": _text(q.get("question_en"), _text(q.get("question"))),
                "topic": _text(q.get("topic")),
            })
    analysis = {
        "interpretation_en": _text(data.get("interpretation_en")),
        "business_objective": _text(data.get("business_objective")),
        "process_area": _text(data.get("process_area"), "Requires consultant confirmation"),
        "expected_system_behavior": _text(data.get("expected_system_behavior")),
        "confirmed_facts": _str_list(data.get("confirmed_facts")),
        "business_rules": _str_list(data.get("business_rules")),
        "ambiguities": _str_list(data.get("ambiguities")),
        "missing_information": _str_list(data.get("missing_information")),
        "contradictions": _str_list(data.get("contradictions")),
        "clarification_questions": questions[:5],
        "consultant_investigation": _str_list(data.get("consultant_investigation")),
        "assumptions": _str_list(data.get("assumptions")),
        "proposed_recommendations": _str_list(data.get("proposed_recommendations")),
        "unresolved_items": _str_list(data.get("unresolved_items")),
        "ready_for_documentation": data.get("ready_for_documentation") is True,
        "readiness_reason": _text(data.get("readiness_reason")),
    }
    if not analysis["business_objective"] or not analysis["interpretation_en"]:
        return None
    return analysis


def _conversation_text(requirement, language, history):
    lines = [
        f'Original requirement (BR-001, as spoken/typed by the user): "{requirement}"',
        f"User's preferred language: {LANGUAGE_LABELS[language]}",
    ]
    if history:
        lines.append("\nClarification so far:")
        for i, turn in enumerate(history, 1):
            answer = turn["answer"] or "(skipped — no answer given)"
            lines.append(f"Q{i} [{turn.get('topic') or 'general'}]: {turn['question_en']}\nA{i}: {answer}")
    else:
        lines.append("\nNo clarification questions have been answered yet.")
    return "\n".join(lines)


def analyze(requirement, language, history):
    guide = prompts.LANGUAGE_GUIDE[language]
    messages = [
        {"role": "system", "content": prompts.CONSULTANT_PERSONA},
        {"role": "user", "content": prompts.ANALYSIS_INSTRUCTIONS.replace("{language_guide}", guide)
            + "\n\n" + _conversation_text(requirement, language, history)},
    ]
    for _ in range(2):  # one retry on malformed output
        response = _call(
            client().chat.completions,
            model=CHAT_MODEL,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=4000,
            temperature=0.2,
        )
        analysis = normalize_analysis(_parse_json(response.choices[0].message.content))
        if analysis:
            return analysis
    raise KiviError("The AI returned an incomplete analysis. Please try again.", 502)


def readiness(analysis, history):
    """App-level readiness check; the model's flag alone is not trusted."""
    has_core = bool(analysis["business_objective"] and analysis["expected_system_behavior"])
    answered = sum(1 for t in history if t["answer"])
    model_ready = analysis["ready_for_documentation"]
    no_questions = not analysis["clarification_questions"]
    if has_core and (model_ready or no_questions) and (answered > 0 or no_questions):
        return "ready"
    return "clarification"


# ---------------------------------------------------------------- documents

def _bullets(items):
    return "\n".join(f"- {i}" for i in items) if items else "- None recorded"


def _context_text(requirement, language, history, analysis):
    a = analysis
    return "\n".join([
        _conversation_text(requirement, language, history),
        "",
        "Analysis (validated):",
        f"English interpretation: {a['interpretation_en']}",
        f"Business objective: {a['business_objective']}",
        f"Process area (proposed): {a['process_area']}",
        f"Expected system behaviour: {a['expected_system_behavior']}",
        "Confirmed facts:\n" + _bullets(a["confirmed_facts"]),
        "Business rules:\n" + _bullets(a["business_rules"]),
        "Contradictions:\n" + _bullets(a["contradictions"]),
        "Assumptions:\n" + _bullets(a["assumptions"]),
        "Proposed recommendations (not confirmed):\n" + _bullets(a["proposed_recommendations"]),
        "Consultant investigation items:\n" + _bullets(a["consultant_investigation"]),
        "Unresolved items:\n" + _bullets(a["unresolved_items"] + a["missing_information"]),
    ])


def _clean_markdown(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", text).strip()
    # The application writes the title; drop any title the model added.
    lines = text.splitlines()
    while lines and (lines[0].startswith("# ") or not lines[0].strip()):
        lines.pop(0)
    return "\n".join(lines).strip()


def _header(spec, requirement, language, analysis):
    open_items = analysis["unresolved_items"] + analysis["missing_information"]
    status = f"DRAFT — {len(open_items)} open item(s), requires consultant review" if open_items \
        else "DRAFT — requires consultant review"
    today = datetime.date.today().isoformat()
    quoted = requirement.replace("\n", " ")
    parts = [
        f"# {spec['name']} ({spec['short']})",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Document ID | Kivi-{spec['short']}-BR-001 |",
        "| Business requirement | BR-001 |",
        "| Module | SAP EWM |",
        "| Version | Draft 0.1 |",
        f"| Status | {status} |",
        f"| Generated | {today} |",
        f"| Prepared by | Kivi — AI-assisted ({CHAT_MODEL} via Sarvam AI) |",
        f"| Source language | {LANGUAGE_LABELS[language]} |",
        "",
        f"> **Original requirement (verbatim):** {quoted}",
        ">",
        f"> **English interpretation:** {analysis['interpretation_en']}",
        "",
        "> **AI-assisted draft.** Content was generated from the captured requirement and clarification answers. "
        "It has not been approved by the business and has not been validated against an SAP system. "
        "Items labelled PROPOSED or UNKNOWN require consultant validation.",
    ]
    if open_items:
        parts += ["", "**Open items at time of generation:**", "", _bullets(open_items)]
    return "\n".join(parts)


def generate_document(doc_type, requirement, language, history, analysis, related):
    spec = prompts.DOC_SPECS[doc_type]
    related_text = ""
    for key, content in related.items():
        if content:
            related_text += (f"Already drafted {prompts.DOC_SPECS[key]['short']} (keep identifiers and decisions "
                             f"consistent with it; flag any discrepancy rather than silently changing decisions):\n"
                             f"<<<\n{content}\n>>>\n\n")
    user = "\n\n".join([
        spec["instructions"],
        prompts.DOC_CONTROL_NOTE,
        prompts.DOC_INSTRUCTIONS_TAIL.format(
            context=_context_text(requirement, language, history, analysis),
            related=related_text,
            short=spec["short"],
        ),
    ])
    response = _call(
        client().chat.completions,
        model=CHAT_MODEL,
        messages=[{"role": "system", "content": prompts.CONSULTANT_PERSONA}, {"role": "user", "content": user}],
        max_tokens=8000,
        temperature=0.2,
    )
    choice = response.choices[0]
    body = _clean_markdown(choice.message.content)
    if len(body) < 200:
        raise KiviError(f"The AI returned an empty {spec['short']}. Please regenerate it.", 502)
    truncated = getattr(choice, "finish_reason", None) == "length"
    if truncated:
        body += "\n\n> **Note:** This document was cut off by the model's length limit. Regenerate it for a complete version."
    return _header(spec, requirement, language, analysis) + "\n\n---\n\n" + body, truncated
