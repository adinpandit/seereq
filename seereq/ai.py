"""Sarvam AI integration: speech-to-text, requirement analysis and document generation."""

import json
import os
import re

import httpx
from sarvamai import SarvamAI
from sarvamai.core.api_error import ApiError

from . import baseline, documents, prompts
from . import model as rm

# sarvam-105b reasons at length and often exhausts max_tokens on these prompts;
# the conversations variant answers directly and is ~30x faster here.
CHAT_MODEL = "sarvam-105b-conversations"
STT_MODEL = "saaras:v3"
TIMEOUT_SECONDS = 180

LANGUAGES = {"hinglish", "hindi", "english"}
LANGUAGE_LABELS = {"hinglish": "Hinglish", "hindi": "Hindi", "english": "English"}


class SeeReqError(Exception):
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
        raise SeeReqError("Sarvam AI is not configured. Add SARVAM_API_KEY to .env and restart the server.", 503)
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
            raise SeeReqError("Sarvam AI rejected the API key. Check SARVAM_API_KEY in .env.", 502)
        if code == 402:
            raise SeeReqError("Your Sarvam AI account has no credits left. Add credits in the Sarvam dashboard.", 502)
        if code == 429:
            raise SeeReqError("Sarvam AI rate limit reached. Wait a moment and try again.", 503)
        if code and 400 <= code < 500:
            raise SeeReqError(f"Sarvam AI could not process the request (HTTP {code}).", 502)
        raise SeeReqError("Sarvam AI is temporarily unavailable. Please try again.", 503)
    except httpx.TimeoutException:
        raise SeeReqError("Sarvam AI took too long to respond. Please try again.", 504)
    except httpx.HTTPError:
        raise SeeReqError("Could not reach Sarvam AI. Check your internet connection.", 503)


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


# ---------------------------------------------------------------- model calls

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


def chat_json(user, max_tokens):
    """One JSON-mode call with a single retry on malformed output. Tests replace this function."""
    messages = [{"role": "system", "content": prompts.CONSULTANT_PERSONA}, {"role": "user", "content": user}]
    for _ in range(2):
        response = _call(client().chat.completions, model=CHAT_MODEL, messages=messages,
                         response_format={"type": "json_object"}, max_tokens=max_tokens, temperature=0.2)
        data = _parse_json(response.choices[0].message.content)
        if isinstance(data, dict):
            return data
    raise SeeReqError("The AI returned an incomplete response. Please try again.", 502)


def chat_text(user, max_tokens):
    """One free-text call. Returns (text, truncated). Tests replace this function."""
    messages = [{"role": "system", "content": prompts.CONSULTANT_PERSONA}, {"role": "user", "content": user}]
    response = _call(client().chat.completions, model=CHAT_MODEL, messages=messages,
                     max_tokens=max_tokens, temperature=0.2)
    choice = response.choices[0]
    return choice.message.content or "", getattr(choice, "finish_reason", None) == "length"


# ---------------------------------------------------------------- context rendering

def model_context(m, include_questions=True):
    """The canonical requirement model as the AI sees it. Every call uses this same rendering."""
    lines = [
        f'Original requirement ({m["requirement_id"]}, verbatim): "{m["original_requirement"]}"',
        f"User's preferred language: {LANGUAGE_LABELS.get(m['language'], 'English')}",
        f"English interpretation: {m['interpretation_en'] or 'Not yet analysed'}",
        f"Business objective: {m['business_objective'] or 'Not yet analysed'}",
        f"Process area (proposed): {m['process_area'] or 'Not yet analysed'}",
        f"Expected system behaviour: {m['expected_system_behavior'] or 'Not yet analysed'}",
        "",
        "Business decisions:",
    ]
    live = rm.live_decisions(m)
    lines += [f"- {d['id']} [{d['status'].upper()}; source: {d['source_ref'] or d['source']}] {d['description']}"
              for d in live] or ["- None yet"]
    superseded = [d for d in m["decisions"] if d["status"] == "superseded"]
    if superseded:
        lines += ["", "Superseded decisions (do not use):"]
        lines += [f"- {d['id']} (replaced by {d['superseded_by']}) {d['description']}" for d in superseded]
    lines += ["", "Open items:"]
    lines += [f"- {o['id']} [{o['owner']}{', blocking' if o['blocking'] else ''}] {o['description']}"
              + (f" — why it matters: {o['why_it_matters']}" if o["why_it_matters"] else "")
              for o in rm.active_open_items(m)] or ["- None"]
    resolved = [o for o in m["open_items"] if o["status"] == "resolved"]
    if resolved:
        lines += ["", "Resolved open items:"]
        lines += [f"- {o['id']} resolved by {o['resolved_by']}: {o['description']}" for o in resolved]
    if include_questions:
        lines += ["", "Clarification record (verbatim):"]
        lines += [f"{c['id']} (round {c['round']}) [{c['topic'] or 'general'}] Q: {c['question_en']}\n"
                  f"   A: {c['answer'] if c['status'] == 'answered' else 'SKIPPED — no answer given'}"
                  for c in m["clarifications"]] or ["None yet."]
    return "\n".join(lines)


def _plan_context(plan):
    return json.dumps({k: plan[k] for k in ("process_steps", "functional_requirements", "acceptance_criteria",
                                            "test_scenarios", "config_activities")}, ensure_ascii=False)


# ---------------------------------------------------------------- analysis

def analyze(requirement, language, model_in, answers):
    """Record answers (when submitted), ask the AI for an updated model, and merge it deterministically.

    answers=None re-runs the analysis without touching pending questions; a dict submits the current round.
    """
    m = model_in or rm.new_model(requirement, language)
    requirement_changed = m["original_requirement"] != requirement
    m.update(original_requirement=requirement, language=language)
    if answers is not None:
        before = {c["id"] for c in m["clarifications"]}
        rm.record_answers(m, answers)
        new = [c for c in m["clarifications"] if c["id"] not in before and c["status"] == "answered"]
        if new:
            extract_decisions(m, new)
    prompt = (prompts.ANALYSIS_INSTRUCTIONS.replace("{language_guide}", prompts.LANGUAGE_GUIDE[language])
              + "\n\nCurrent requirement model:\n" + model_context(m))
    if requirement_changed and model_in:
        prompt += "\n\nNote: the user edited the original requirement; decisions sourced from BR-001 may need updating."
    raw = chat_json(prompt, max_tokens=5000)
    rm.merge_analysis(m, raw, requirement_changed=requirement_changed and bool(model_in))
    if not m["business_objective"] or not m["interpretation_en"]:
        raise SeeReqError("The AI returned an incomplete analysis. Please try again.", 502)
    return m


def extract_decisions(m, clarifications):
    """Focused pass that turns each new answer into atomic, confirmed decisions before the wider analysis."""
    record = "\n".join(f"{c['id']} Q: {c['question_en']}\n   A (verbatim): {c['answer']}" for c in clarifications)
    raw = chat_json(prompts.EXTRACTION_INSTRUCTIONS + "\n\nAnswers:\n" + record, max_tokens=3000)
    ids = {c["id"] for c in clarifications}
    items = [{"description": d.get("description"), "source": "clarification", "source_ref": d.get("source_ref"),
              "status": "confirmed", "category": d.get("category")}
             for d in raw.get("decisions") or [] if isinstance(d, dict) and d.get("source_ref") in ids]
    notes = []
    rm.merge_decisions(m, items, notes)
    m["notes"] = notes


# ---------------------------------------------------------------- baseline

def build_baseline(m):
    prompt = prompts.BASELINE_INSTRUCTIONS + "\n\nCanonical requirement model:\n" + model_context(m)
    plan = baseline.validate_plan(chat_json(prompt, max_tokens=8000), m)
    if plan["uncovered_decisions"] or not plan["functional_requirements"]:
        retry = prompt + ("\n\nYour previous baseline did not cover these confirmed decisions with any functional "
                          f"requirement: {', '.join(plan['uncovered_decisions']) or 'all of them'}. "
                          "Return the complete baseline again and cover every confirmed decision.")
        second = baseline.validate_plan(chat_json(retry, max_tokens=8000), m)
        if len(second["uncovered_decisions"]) < len(plan["uncovered_decisions"]) or not plan["functional_requirements"]:
            plan = second
    return plan


# ---------------------------------------------------------------- documents

def _doc_prompt(doc_type, m, plan, feedback=""):
    headings = "\n".join(f"## {t}" for t in documents.section_titles(doc_type)[1:])
    parts = [prompts.DOC_GUIDANCE[doc_type], prompts.DOC_RULES.replace("{headings}", headings),
             "Canonical requirement model (source of truth):\n" + model_context(m),
             "Requirement baseline (use these IDs exactly):\n" + _plan_context(plan)]
    if feedback:
        parts.append("Your previous draft had these problems. Fix them in this version:\n" + feedback)
    return "\n\n".join(parts)


def _clean_markdown(text):
    text = (text or "").strip()
    return re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", text).strip()


def generate_document(doc_type, m, plan, revision=1):
    """Generate one document from the shared model and baseline; repair once if checks fail."""
    best = None
    feedback = ""
    for attempt in range(2):
        body, truncated = chat_text(_doc_prompt(doc_type, m, plan, feedback), max_tokens=8000)
        body = _clean_markdown(body)
        if len(body) < 200:
            if attempt == 0:
                feedback = "The draft was empty. Write all sections."
                continue
            break
        markdown, missing = documents.assemble(doc_type, body, m, plan, revision, CHAT_MODEL)
        issues = documents.check_document(doc_type, markdown, m, plan)
        if truncated:
            issues.append({"level": "warning", "doc": doc_type.upper(),
                           "message": "The model hit its length limit; later sections may be incomplete."})
        score = sum(3 if i["level"] == "error" else 1 for i in issues) + 2 * len(missing)
        if best is None or score < best[0]:
            best = (score, markdown, issues)
        repairable = [i["message"] for i in issues if i["level"] == "error" or "SAP detail" in i["message"]]
        if missing:
            repairable.append("These sections were missing: " + ", ".join(missing))
        if not repairable:
            break
        feedback = "\n".join(f"- {r}" for r in repairable)
    if best is None:
        raise SeeReqError(f"The AI returned an empty {doc_type.upper()}. Please regenerate it.", 502)
    return best[1], best[2]
