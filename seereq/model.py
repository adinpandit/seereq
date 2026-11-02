"""Canonical requirement model: the single source of truth for a requirement.

The AI proposes updates; the merge rules here decide what is accepted. Confirmed business decisions are never
dropped, open items only close when a real clarification answer resolves them, and every answered clarification
is captured by at least one decision.
"""

import re

REQ_ID = "BR-001"
DECISION_STATUSES = {"confirmed", "proposed", "assumption", "superseded"}
SOURCES = {"original_requirement", "clarification", "ai_inference"}
OWNERS = {"business", "consultant"}
MAX_TEXT = 600
MAX_ITEMS = 80

_STOP = set("a an the of to in on for and or is are be should must will can if when then this that it with by as at "
            "from not no system user ko ki ka ke hai hona chahiye se par mein".split())


# ---------------------------------------------------------------- helpers

def _text(value, limit=MAX_TEXT):
    return value.strip()[:limit] if isinstance(value, str) else ""


def _tokens(text):
    return {t for t in re.findall(r"[a-z0-9ऀ-ॿ]+", text.lower()) if t not in _STOP and len(t) > 1}


def similar(a, b, threshold=0.6, subset=True):
    """Cheap duplicate detection for short statements."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold or (subset and (ta <= tb or tb <= ta))


def next_id(prefix, items):
    nums = [int(m.group(1)) for i in items for m in [re.match(rf"{prefix}-(\d+)$", i.get("id", ""))] if m]
    return f"{prefix}-{(max(nums) + 1) if nums else 1:03d}"


def _id(value, prefix):
    return value if isinstance(value, str) and re.fullmatch(rf"{prefix}-\d{{3}}", value) else None


def new_model(requirement, language):
    return {
        "requirement_id": REQ_ID,
        "original_requirement": requirement,
        "language": language,
        "interpretation_en": "",
        "business_objective": "",
        "process_area": "",
        "expected_system_behavior": "",
        "decisions": [],
        "open_items": [],
        "clarifications": [],
        "pending_questions": [],
        "contradictions": [],
        "round": 0,
        "ready": False,
        "readiness_reason": "",
        "notes": [],
    }


# ---------------------------------------------------------------- validation of client-supplied models

def normalize_model(data):
    """Validate a model sent back by the browser. Returns None if it is unusable."""
    if not isinstance(data, dict) or not _text(data.get("original_requirement"), 4000):
        return None
    m = new_model(_text(data.get("original_requirement"), 4000), data.get("language") or "english")
    for key in ("interpretation_en", "business_objective", "process_area", "expected_system_behavior", "readiness_reason"):
        m[key] = _text(data.get(key))
    m["round"] = data.get("round") if isinstance(data.get("round"), int) and 0 <= data["round"] < 100 else 0
    m["ready"] = data.get("ready") is True
    m["contradictions"] = [_text(c) for c in (data.get("contradictions") or [])[:20] if _text(c)]
    m["notes"] = [_text(c) for c in (data.get("notes") or [])[:40] if _text(c)]

    for d in (data.get("decisions") or [])[:MAX_ITEMS]:
        if isinstance(d, dict) and _id(d.get("id"), "DEC") and _text(d.get("description")):
            m["decisions"].append({
                "id": d["id"],
                "description": _text(d["description"]),
                "source": d.get("source") if d.get("source") in SOURCES else "ai_inference",
                "source_ref": _text(d.get("source_ref"), 20),
                "status": d.get("status") if d.get("status") in DECISION_STATUSES else "assumption",
                "category": _text(d.get("category"), 60),
                "superseded_by": _text(d.get("superseded_by"), 20),
            })
    for o in (data.get("open_items") or [])[:MAX_ITEMS]:
        if isinstance(o, dict) and _id(o.get("id"), "OI") and _text(o.get("description")):
            m["open_items"].append({
                "id": o["id"],
                "description": _text(o["description"]),
                "why_it_matters": _text(o.get("why_it_matters")),
                "owner": o.get("owner") if o.get("owner") in OWNERS else "business",
                "blocking": o.get("blocking") is True,
                "status": "resolved" if o.get("status") == "resolved" else "open",
                "resolved_by": _text(o.get("resolved_by"), 20),
            })
    for c in (data.get("clarifications") or [])[:MAX_ITEMS]:
        if isinstance(c, dict) and re.fullmatch(r"Q\d{1,3}", str(c.get("id"))) and _text(c.get("question_en")):
            answer = _text(c.get("answer"), 2000)
            m["clarifications"].append({
                "id": c["id"],
                "question": _text(c.get("question")) or _text(c.get("question_en")),
                "question_en": _text(c["question_en"]),
                "topic": _text(c.get("topic"), 80),
                "addresses": [a for a in (c.get("addresses") or []) if _id(a, "OI")][:5],
                "answer": answer,
                "status": "answered" if answer else "skipped",
                "round": c.get("round") if isinstance(c.get("round"), int) else 0,
            })
    for q in (data.get("pending_questions") or [])[:5]:
        if isinstance(q, dict) and re.fullmatch(r"Q\d{1,3}", str(q.get("id"))) and _text(q.get("question_en")):
            m["pending_questions"].append({
                "id": q["id"],
                "question": _text(q.get("question")) or _text(q.get("question_en")),
                "question_en": _text(q["question_en"]),
                "topic": _text(q.get("topic"), 80),
                "addresses": [a for a in (q.get("addresses") or []) if _id(a, "OI")][:5],
            })
    return m


# ---------------------------------------------------------------- round handling

def record_answers(model, answers):
    """Move pending questions into the clarification record. Blank answers are recorded as skipped."""
    if not model["pending_questions"]:
        return model
    model["round"] += 1
    for q in model["pending_questions"]:
        answer = _text(answers.get(q["id"]), 2000) if isinstance(answers, dict) else ""
        model["clarifications"].append({**q, "answer": answer, "status": "answered" if answer else "skipped",
                                        "round": model["round"]})
    model["pending_questions"] = []
    return model


def answered_ids(model):
    return {c["id"] for c in model["clarifications"] if c["status"] == "answered"}


def merge_decisions(model, items, notes, requirement_changed=False):
    """Merge proposed decisions. Confirmed decisions keep their wording and source; nothing confirmed is dropped."""
    answered = answered_ids(model)
    clar_round = {c["id"]: c["round"] for c in model["clarifications"]}
    by_id = {d["id"]: d for d in model["decisions"]}
    for item in items:
        if not isinstance(item, dict) or not _text(item.get("description")):
            continue
        desc = _text(item["description"])
        source = item.get("source") if item.get("source") in SOURCES else "ai_inference"
        source_ref = _text(item.get("source_ref"), 20)
        status = item.get("status") if item.get("status") in DECISION_STATUSES else "assumption"

        # Confirmed requires a real source: the original requirement or an answered clarification.
        if status == "confirmed":
            valid = (source == "original_requirement" and source_ref in ("", REQ_ID)) or \
                    (source == "clarification" and source_ref in answered)
            if not valid:
                status = "assumption" if source == "ai_inference" else "proposed"
                notes.append(f"'{desc[:60]}…' was not marked confirmed: no matching user answer ({source_ref or 'none'}).")
        if source == "original_requirement":
            source_ref = REQ_ID

        ref = _id(item.get("ref"), "DEC")
        existing = by_id.get(ref) if ref else None
        if existing is None:
            existing = next((d for d in model["decisions"] if d["status"] != "superseded"
                             and similar(d["description"], desc, 0.75, subset=False)), None)
        if existing:
            if status == "superseded":
                later = _text(item.get("superseded_by"), 20)
                if existing["status"] == "confirmed":
                    # A confirmed decision can only be replaced by a later user answer.
                    if later in answered and clar_round.get(later, 0) > clar_round.get(existing["source_ref"], 0):
                        existing.update(status="superseded", superseded_by=later)
                    elif requirement_changed and existing["source"] == "original_requirement":
                        existing.update(status="superseded", superseded_by="requirement edited")
                    else:
                        notes.append(f"{existing['id']} kept: a confirmed decision cannot be withdrawn without a later answer.")
                else:
                    existing.update(status="superseded", superseded_by=later or "analysis")
                continue
            if existing["status"] == "confirmed" and status != "confirmed":
                continue  # never downgrade a confirmed decision
            if existing["status"] == "confirmed" and existing["source_ref"] != source_ref:
                # Never rewrite a confirmed decision with content from a different source; keep both.
                if similar(existing["description"], desc, 0.85, subset=False):
                    continue
                existing = None
            else:
                existing.update(description=desc, status=status,
                                category=_text(item.get("category"), 60) or existing["category"])
                if status == "confirmed":
                    existing.update(source=source, source_ref=source_ref)
                continue
        if status != "superseded":
            new = {"id": next_id("DEC", model["decisions"]), "description": desc, "source": source,
                   "source_ref": source_ref, "status": status, "category": _text(item.get("category"), 60),
                   "superseded_by": ""}
            model["decisions"].append(new)
            by_id[new["id"]] = new


def merge_analysis(model, raw, requirement_changed=False):
    """Merge an AI analysis into the model using deterministic rules. Returns the updated model."""
    notes = []
    answered = answered_ids(model)
    clar_round = {c["id"]: c["round"] for c in model["clarifications"]}

    for key in ("interpretation_en", "business_objective", "process_area", "expected_system_behavior"):
        value = _text(raw.get(key))
        if value:
            model[key] = value
    model["contradictions"] = [_text(c) for c in (raw.get("contradictions") or [])[:10] if _text(c)]

    merge_decisions(model, raw.get("decisions") or [], notes, requirement_changed)

    # Safety net: the original requirement itself is always a confirmed decision.
    if model["interpretation_en"] and not any(d["source"] == "original_requirement" and d["status"] == "confirmed"
                                              for d in model["decisions"]):
        model["decisions"].insert(0, {
            "id": next_id("DEC", model["decisions"]), "description": model["interpretation_en"],
            "source": "original_requirement", "source_ref": REQ_ID, "status": "confirmed",
            "category": "Original requirement", "superseded_by": "",
        })

    # Safety net: every answered clarification must be captured by at least one decision.
    cited = {d["source_ref"] for d in model["decisions"] if d["status"] == "confirmed"}
    for c in model["clarifications"]:
        if c["status"] == "answered" and c["id"] not in cited:
            model["decisions"].append({
                "id": next_id("DEC", model["decisions"]),
                "description": f"Answer to “{c['question_en']}”: {c['answer']}",
                "source": "clarification", "source_ref": c["id"], "status": "confirmed",
                "category": "Captured answer (not structured by AI)", "superseded_by": "",
            })
            notes.append(f"Answer {c['id']} was captured verbatim because the AI did not extract a decision from it.")

    # ---- open items
    oi_by_id = {o["id"]: o for o in model["open_items"]}
    for item in raw.get("open_items") or []:
        if not isinstance(item, dict) or not _text(item.get("description")):
            continue
        desc = _text(item["description"])
        ref = _id(item.get("ref"), "OI")
        existing = oi_by_id.get(ref) if ref else None
        if existing is None:
            existing = next((o for o in model["open_items"] if similar(o["description"], desc)), None)
        wants_resolved = item.get("status") == "resolved"
        resolved_by = _text(item.get("resolved_by"), 20)
        if existing:
            if wants_resolved and existing["status"] == "open":
                if resolved_by in answered:
                    existing.update(status="resolved", resolved_by=resolved_by)
                else:
                    notes.append(f"{existing['id']} stays open: it was not resolved by a user answer.")
            elif not wants_resolved and existing["status"] == "open":
                existing["why_it_matters"] = _text(item.get("why_it_matters")) or existing["why_it_matters"]
                existing["blocking"] = item.get("blocking") is True
            continue
        if wants_resolved:
            continue  # nothing to close
        new = {"id": next_id("OI", model["open_items"]), "description": desc,
               "why_it_matters": _text(item.get("why_it_matters")),
               "owner": item.get("owner") if item.get("owner") in OWNERS else "business",
               "blocking": item.get("blocking") is True, "status": "open", "resolved_by": ""}
        model["open_items"].append(new)
        oi_by_id[new["id"]] = new

    # ---- next questions
    questions = []
    skipped_ois = {a for c in model["clarifications"] if c["status"] == "skipped" for a in c["addresses"]}
    for q in raw.get("clarification_questions") or []:
        if not isinstance(q, dict) or not _text(q.get("question")):
            continue
        q_en = _text(q.get("question_en")) or _text(q["question"])
        # A skipped question is never re-asked (its open item simply stays open); an answered one only when the
        # AI explains why the answer was ambiguous.
        repeats = [c for c in model["clarifications"] if similar(q_en, c["question_en"], 0.7)]
        if any(c["status"] == "skipped" for c in repeats) or (repeats and not _text(q.get("reask_reason"))):
            notes.append(f"Dropped a repeated question: “{q_en[:70]}”.")
            continue
        if q.get("addresses") and all(a in skipped_ois for a in q["addresses"]):
            notes.append(f"Did not re-ask skipped open item(s) {', '.join(q['addresses'])}; they remain open.")
            continue
        addresses = []
        for a in q.get("addresses") or []:
            if _id(a, "OI") in oi_by_id:
                addresses.append(a)
            elif isinstance(a, str):
                match = next((o["id"] for o in model["open_items"] if similar(o["description"], a)), None)
                if match:
                    addresses.append(match)
        targets = [oi_by_id[a] for a in addresses]
        if targets and all(t["owner"] == "consultant" or t["status"] == "resolved" for t in targets):
            continue  # business users are not asked consultant-owned or already resolved questions
        questions.append({"question": _text(q["question"]), "question_en": q_en,
                          "topic": _text(q.get("topic"), 80), "addresses": addresses})
    start = len(model["clarifications"]) + 1
    model["pending_questions"] = [{**q, "id": f"Q{start + i}"} for i, q in enumerate(questions[:5])]

    # ---- readiness (the AI's flag is advisory only)
    blocking = [o for o in active_open_items(model) if o["blocking"] and o["owner"] == "business"]
    confirmed = [d for d in model["decisions"] if d["status"] == "confirmed"]
    model["ready"] = bool(confirmed and model["business_objective"] and not blocking
                          and (raw.get("ready_for_documentation") is True or not model["pending_questions"]))
    model["readiness_reason"] = _text(raw.get("readiness_reason")) if model["ready"] else (
        f"{len(blocking)} blocking business question(s) remain open." if blocking
        else _text(raw.get("readiness_reason")) or "Further clarification is recommended.")
    model["notes"] = notes[-20:]
    return model


def active_open_items(model):
    return [o for o in model["open_items"] if o["status"] == "open"]


def live_decisions(model):
    return [d for d in model["decisions"] if d["status"] != "superseded"]
