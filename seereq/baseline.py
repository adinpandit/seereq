"""Requirement baseline: process steps, FR, AC, test scenarios and configuration activities shared by all documents.

The AI drafts the baseline once; validate_plan() renumbers IDs, drops references to things that do not exist,
enforces status rules, and records coverage gaps instead of inventing mappings. The render_* functions turn the
validated baseline into the tables and diagram that every document embeds verbatim.
"""

import hashlib
import json
import re

from .model import active_open_items, live_decisions

STEP_KINDS = {"start", "task", "decision", "end"}
STATUSES = {"confirmed", "proposed", "assumption", "open"}
TEST_TYPES = {"positive", "negative", "boundary", "exception"}
APPROACH_TYPES = {
    "standard_configuration": "Standard configuration (to be validated)",
    "possible_custom_development": "Possible custom development",
    "unresolved": "Unresolved — investigation required",
}
LABEL = {"confirmed": "CONFIRMED", "proposed": "PROPOSED", "assumption": "ASSUMPTION", "open": "OPEN"}
CONSULTANT = "CONSULTANT VALIDATION REQUIRED"


def _t(value, limit=500):
    return re.sub(r"\s+", " ", value).strip()[:limit] if isinstance(value, str) else ""


def _list(value):
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _renumber(items, prefix):
    """Assign PREFIX-001.. in order; return (items, old->new map)."""
    mapping = {}
    for i, item in enumerate(items, 1):
        new = f"{prefix}-{i:03d}"
        old = _t(item.get("id"), 20)
        if old and old not in mapping:
            mapping[old] = new
        item["id"] = new
    return items, mapping


def validate_plan(raw, model):
    warnings = []
    decisions = {d["id"]: d for d in live_decisions(model)}
    confirmed = {i for i, d in decisions.items() if d["status"] == "confirmed"}
    open_ids = {o["id"] for o in active_open_items(model)}

    def refs(values, valid, what, owner):
        good = []
        for v in _list(values):
            if v in valid:
                good.append(v)
            else:
                warnings.append(f"{owner}: removed reference to unknown {what} '{v}'.")
        return list(dict.fromkeys(good))

    # ---- process steps
    steps = [s for s in (raw.get("process_steps") or []) if isinstance(s, dict) and _t(s.get("action"))]
    steps, step_map = _renumber(steps, "BPD")
    for s in steps:
        s["kind"] = s.get("kind") if s.get("kind") in STEP_KINDS else "task"
        s["actor"] = _t(s.get("actor"), 60) or "Not specified"
        s["action"] = _t(s.get("action"), 300)
        s["decision_refs"] = refs(s.get("decision_refs"), decisions, "decision", s["id"])
        s["open_refs"] = refs(s.get("open_refs"), open_ids, "open item", s["id"])
        nxt = []
        for edge in s.get("next") or []:
            target = step_map.get(_t(edge.get("to"), 20)) if isinstance(edge, dict) else None
            if target:
                nxt.append({"to": target, "label": _t(edge.get("label"), 40)})
            else:
                warnings.append(f"{s['id']}: removed a flow link to a step that does not exist.")
        s["next"] = nxt
        status = s.get("status") if s.get("status") in STATUSES else "assumption"
        if status == "confirmed" and not (s["decision_refs"] and any(r in confirmed for r in s["decision_refs"])):
            status = "assumption"
        s["status"] = "open" if s["open_refs"] and status != "confirmed" else status
    if steps and not any(s["kind"] == "decision" and len(s["next"]) >= 2 for s in steps):
        warnings.append("Process flow has no decision with alternative paths; success and failure paths need review.")

    # ---- functional requirements
    frs = [f for f in (raw.get("functional_requirements") or []) if isinstance(f, dict) and _t(f.get("description"))]
    frs, fr_map = _renumber(frs, "FR")
    for f in frs:
        f["title"] = _t(f.get("title"), 80) or _t(f["description"], 60)
        f["description"] = _t(f["description"], 600)
        f["decision_refs"] = refs(f.get("decision_refs"), decisions, "decision", f["id"])
        f["bpd_refs"] = [step_map[r] for r in _list(f.get("bpd_refs")) if r in step_map]
        f["open_refs"] = refs(f.get("open_refs"), open_ids, "open item", f["id"])
        status = f.get("business_status") if f.get("business_status") in STATUSES else "proposed"
        if status == "confirmed" and not (f["decision_refs"] and all(r in confirmed for r in f["decision_refs"])):
            status = "proposed"
        if f["open_refs"] and status != "confirmed":
            status = "open"
        f["business_status"] = status
        if not f["decision_refs"]:
            warnings.append(f"{f['id']} is not traced to any business decision — review whether it is needed.")

    # ---- acceptance criteria and tests
    acs = [a for a in (raw.get("acceptance_criteria") or []) if isinstance(a, dict) and _t(a.get("then"))]
    acs, ac_map = _renumber(acs, "AC")
    kept_acs = []
    for a in acs:
        a["fr_refs"] = [fr_map[r] for r in _list(a.get("fr_refs")) if r in fr_map]
        a.update(given=_t(a.get("given"), 300), when=_t(a.get("when"), 300), then=_t(a.get("then"), 300))
        if a["fr_refs"]:
            kept_acs.append(a)
        else:
            warnings.append(f"An acceptance criterion was dropped because it did not trace to a functional requirement.")
    acs, ac_map2 = _renumber(kept_acs, "AC")
    ac_final = {old: ac_map2.get(mid) for old, mid in ac_map.items() if ac_map2.get(mid)}

    tests = [t for t in (raw.get("test_scenarios") or []) if isinstance(t, dict) and _t(t.get("scenario"))]
    kept_tests = []
    for t in tests:
        t["type"] = t.get("type") if t.get("type") in TEST_TYPES else "positive"
        t["fr_refs"] = [fr_map[r] for r in _list(t.get("fr_refs")) if r in fr_map]
        t["ac_refs"] = [ac_final[r] for r in _list(t.get("ac_refs")) if r in ac_final]
        t.update(scenario=_t(t["scenario"], 300), expected=_t(t.get("expected"), 300))
        if t["fr_refs"]:
            kept_tests.append(t)
        else:
            warnings.append("A test scenario was dropped because it did not trace to a functional requirement.")
    tests, _ = _renumber(kept_tests, "TS")

    # ---- configuration / implementation activities
    cfgs = [c for c in (raw.get("config_activities") or []) if isinstance(c, dict) and _t(c.get("title"))]
    cfgs, _ = _renumber(cfgs, "CFG")
    for c in cfgs:
        c["fr_refs"] = [fr_map[r] for r in _list(c.get("fr_refs")) if r in fr_map]
        c["open_refs"] = refs(c.get("open_refs"), open_ids, "open item", c["id"])
        c["approach_type"] = c.get("approach_type") if c.get("approach_type") in APPROACH_TYPES else "unresolved"
        for key in ("title", "purpose", "prerequisites", "decision_needed", "proposed_approach", "investigate",
                    "verification", "dependencies"):
            c[key] = _t(c.get(key), 500)
        values = []
        for v in c.get("config_values") or []:
            if isinstance(v, dict) and _t(v.get("setting")):
                user_given = v.get("status") == "confirmed" and _t(v.get("value"))
                values.append({"setting": _t(v["setting"], 120),
                               "value": _t(v.get("value"), 120) if user_given else "Not provided",
                               "status": "confirmed" if user_given else "open"})
        c["config_values"] = values
        if not c["fr_refs"]:
            warnings.append(f"{c['id']} does not reference a functional requirement — flagged for review.")

    # ---- coverage
    covered = {r for f in frs for r in f["decision_refs"]}
    uncovered = [d for d in confirmed if d not in covered]
    for d in uncovered:
        warnings.append(f"Confirmed decision {d} is not covered by any functional requirement.")
    for f in frs:
        if not any(f["id"] in a["fr_refs"] for a in acs):
            warnings.append(f"{f['id']} has no acceptance criterion.")
        if not any(f["id"] in t["fr_refs"] for t in tests):
            warnings.append(f"{f['id']} has no test scenario.")

    plan = {"process_steps": steps, "functional_requirements": frs, "acceptance_criteria": acs,
            "test_scenarios": tests, "config_activities": cfgs, "warnings": list(dict.fromkeys(warnings)),
            "uncovered_decisions": sorted(uncovered)}
    plan["fingerprint"] = fingerprint(model, plan)
    return plan


def fingerprint(model, plan):
    """Short hash identifying the requirement state a set of documents was generated from."""
    basis = {"req": model["original_requirement"], "dec": live_decisions(model), "oi": model["open_items"],
             "plan": {k: plan[k] for k in ("process_steps", "functional_requirements", "config_activities")}}
    return hashlib.sha1(json.dumps(basis, sort_keys=True).encode()).hexdigest()[:8]


def known_ids(model, plan):
    ids = {d["id"] for d in model["decisions"]} | {o["id"] for o in model["open_items"]} | {model["requirement_id"]}
    for key in ("process_steps", "functional_requirements", "acceptance_criteria", "test_scenarios", "config_activities"):
        ids |= {i["id"] for i in plan.get(key, [])}
    return ids


# ---------------------------------------------------------------- rendering

def _cell(value):
    text = value if isinstance(value, str) else ", ".join(value) if isinstance(value, list) else str(value)
    return (text or "—").replace("|", "/").replace("\n", " ")


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _source(d):
    if d["source"] == "original_requirement":
        return "Original requirement (BR-001)"
    if d["source"] == "clarification":
        return f"Clarification answer {d['source_ref']}"
    return "AI inference"


def render_business_rules(model):
    rows = [(d["id"], d["description"], _source(d), LABEL.get(d["status"], d["status"].upper()))
            for d in live_decisions(model) if d["status"] in ("confirmed", "proposed")]
    return table(["ID", "Business decision / rule", "Source", "Status"], rows) if rows else "No business decisions recorded."


def render_assumptions(model):
    rows = [(d["id"], d["description"], "ASSUMPTION — requires business confirmation")
            for d in live_decisions(model) if d["status"] == "assumption"]
    return table(["ID", "Assumption", "Status"], rows) if rows else "No assumptions recorded."


def render_open_items(model):
    active = active_open_items(model)
    parts = []
    if active:
        parts.append(table(["ID", "Open item", "Why it matters", "Owner", "Blocking"],
                           [(o["id"], o["description"], o["why_it_matters"] or "Requires confirmation",
                             "Business" if o["owner"] == "business" else "SAP consultant",
                             "Yes" if o["blocking"] else "No") for o in active]))
    else:
        parts.append("No open items remain.")
    resolved = [o for o in model["open_items"] if o["status"] == "resolved"]
    if resolved:
        parts.append("Resolved during clarification:\n\n" + table(
            ["ID", "Item", "Resolved by"], [(o["id"], o["description"], f"Answer {o['resolved_by']}") for o in resolved]))
    return "\n\n".join(parts)


def render_process_steps(plan):
    rows = [(s["id"], s["actor"], s["action"],
             "; ".join(f"{e['label']} → {e['to']}" if e["label"] else e["to"] for e in s["next"]) or "End",
             s["decision_refs"] + s["open_refs"], LABEL[s["status"]]) for s in plan["process_steps"]]
    return table(["Step", "Actor", "Activity", "Next", "Traces to", "Status"], rows) if rows else "Process steps not provided."


def render_frs(plan):
    rows = [(f["id"], f"**{f['title']}** — {f['description']}", f["decision_refs"] + f["bpd_refs"],
             LABEL[f["business_status"]], CONSULTANT) for f in plan["functional_requirements"]]
    return table(["ID", "Functional requirement", "Traces to", "Business status", "Implementation"], rows) \
        if rows else "No functional requirements defined."


def render_acs(plan):
    rows = [(a["id"], a["given"], a["when"], a["then"], a["fr_refs"]) for a in plan["acceptance_criteria"]]
    return ("**Acceptance criteria**\n\n" + table(["ID", "Given", "When", "Then", "Verifies"], rows)) if rows \
        else "No acceptance criteria defined."


def render_tests(plan):
    rows = [(t["id"], t["type"].capitalize(), t["scenario"], t["expected"], t["fr_refs"] + t["ac_refs"])
            for t in plan["test_scenarios"]]
    return ("**Test scenarios**\n\n" + table(["ID", "Type", "Scenario", "Expected result", "Traces to"], rows)) if rows \
        else "No test scenarios defined."


def render_fr_reference(plan):
    frs = {f["id"]: f for f in plan["functional_requirements"]}
    rows = []
    for c in plan["config_activities"]:
        linked = [frs[r] for r in c["fr_refs"] if r in frs]
        rows.append((c["id"], c["fr_refs"] or "⚠ Not traced — review",
                     sorted({b for f in linked for b in f["bpd_refs"]}),
                     sorted({d for f in linked for d in f["decision_refs"]})))
    return table(["Activity", "Functional requirements", "BPD steps", "Business decisions"], rows) if rows \
        else "No configuration activities defined."


def _with_refs(text, refs):
    missing = [r for r in refs if r not in text]
    return text + (f" (open items: {', '.join(missing)})" if missing else "")


def render_cfg_activities(plan):
    blocks = []
    for c in plan["config_activities"]:
        blocks.append(f"### {c['id']} — {c['title']}\n\n" + table(["Attribute", "Detail"], [
            ("Business purpose", c["purpose"] or "Not provided"),
            ("Related requirements", c["fr_refs"] or "⚠ Not traced — review"),
            ("Prerequisites", c["prerequisites"] or "Not provided"),
            ("Decision required", c["decision_needed"] or "Not provided"),
            ("Approach type", APPROACH_TYPES[c["approach_type"]]),
            ("Proposed approach", (c["proposed_approach"] + " — PROPOSED") if c["proposed_approach"] else "Not provided"),
            ("To investigate", c["investigate"] or "Not provided"),
            ("Verification", c["verification"] or "Not provided"),
            ("Dependencies", _with_refs(c["dependencies"] or "None identified", c["open_refs"])),
            ("Status", CONSULTANT),
        ]))
    return "\n\n".join(blocks) if blocks else "No configuration activities defined."


def render_cfg_decisions(plan):
    rows = [(c["id"], c["decision_needed"] or c["title"], c["fr_refs"], APPROACH_TYPES[c["approach_type"]],
             "OPEN" if c["open_refs"] else CONSULTANT) for c in plan["config_activities"]]
    return table(["ID", "Decision required", "Supports", "Approach type", "Status"], rows) if rows \
        else "No configuration decisions defined."


def render_cfg_values(plan):
    rows = [(c["id"], v["setting"], v["value"], LABEL[v["status"]]) for c in plan["config_activities"]
            for v in c["config_values"]]
    return table(["Activity", "Setting", "Value", "Status"], rows) if rows else \
        "No configuration values have been confirmed. All values are **Not provided** and must be determined by the SAP consultant."


def render_checklist(plan, model):
    items = [f"- [ ] {c['id']}: {c['verification'] or 'Verify ' + c['title']}" for c in plan["config_activities"]]
    items += [f"- [ ] Resolve {o['id']}: {o['description']}" for o in active_open_items(model) if o["owner"] == "consultant"]
    items.append("- [ ] Confirm no proposed approach in this document is treated as validated before system testing.")
    return "\n".join(items)


def render_traceability(model, plan):
    frs = plan["functional_requirements"]
    rows = []
    for d in live_decisions(model):
        if d["status"] not in ("confirmed", "proposed"):
            continue
        fr_ids = [f["id"] for f in frs if d["id"] in f["decision_refs"]]
        steps = sorted({b for f in frs if f["id"] in fr_ids for b in f["bpd_refs"]} |
                       {s["id"] for s in plan["process_steps"] if d["id"] in s["decision_refs"]})
        acs = [a["id"] for a in plan["acceptance_criteria"] if set(a["fr_refs"]) & set(fr_ids)]
        tests = [t["id"] for t in plan["test_scenarios"] if set(t["fr_refs"]) & set(fr_ids)]
        cfgs = [c["id"] for c in plan["config_activities"] if set(c["fr_refs"]) & set(fr_ids)]
        rows.append((d["id"], LABEL[d["status"]], steps or "—", fr_ids or "⚠ Not traced — review", acs or "—",
                     tests or "—", cfgs or "—"))
    return table(["Decision", "Status", "BPD steps", "FR", "AC", "Tests", "CFG"], rows) if rows else \
        "No decisions to trace."


# ---------------------------------------------------------------- mermaid

def _mermaid_label(step):
    """Short node label; the full activity text is in the process-step table."""
    limit = 48 if step["kind"] == "decision" else 72
    text = re.sub(r"[\"`<>{}\[\]()|#;]", "", f"{step['id']}: {step['action']}")
    text = text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) > 28 and line:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    lines.append(line)
    return "<br/>".join(lines)


def render_mermaid(plan):
    steps = plan["process_steps"]
    if not steps:
        return None
    out = ["flowchart TD"]
    for s in steps:
        node, label = s["id"].replace("-", "_"), _mermaid_label(s)
        shape = {"start": f'(["{label}"])', "end": f'(["{label}"])', "decision": f'{{"{label}"}}'}.get(s["kind"], f'["{label}"]')
        out.append(f"    {node}{shape}")
    for s in steps:
        for e in s["next"]:
            label = re.sub(r"[\"`<>{}\[\]()|#;]", "", e["label"])
            arrow = f" -->|{label}| " if label else " --> "
            out.append(f"    {s['id'].replace('-', '_')}{arrow}{e['to'].replace('-', '_')}")
    return "\n".join(out)
