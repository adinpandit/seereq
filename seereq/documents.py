"""Document assembly and traceability checks.

The AI writes the narrative of each section; the application owns the section structure, the document header and
every table that carries IDs, so all three documents show the same decisions, requirements and open items.
"""

import datetime
import re

from . import baseline as bl
from .model import active_open_items

# (title, [blocks inserted at the top of the section]). Section 1 is always written by the application.
SECTIONS = {
    "bpd": [
        ("Document Information", []),
        ("Business Background and Objective", []),
        ("Scope and Exclusions", []),
        ("Stakeholders and Responsibilities", []),
        ("Preconditions and Triggers", []),
        ("Current Business Process", []),
        ("Proposed Business Process", []),
        ("Step-by-Step Process Flow", ["diagram", "steps"]),
        ("Business Rules", ["rules"]),
        ("Exception Scenarios and Recovery Paths", []),
        ("Dependencies", []),
        ("Assumptions and Open Questions", ["assumptions", "open_items"]),
        ("Expected Business Outcomes", []),
    ],
    "fsd": [
        ("General Information", []),
        ("Business Requirement", ["rules"]),
        ("Current Functionality", []),
        ("Desired Functionality", ["frs"]),
        ("Process Flow Diagram", ["diagram"]),
        ("Assumptions and Dependencies", ["assumptions"]),
        ("Out of Scope", []),
        ("Open Items", ["open_items"]),
        ("Master Data", []),
        ("Technical Detail", []),
        ("Retention", []),
        ("Security Requirements", []),
        ("Recovery, Restart and Logs", []),
        ("Translations", []),
        ("Currency and Units of Measure", []),
        ("Performance Requirements", []),
        ("Error Handling, Escalation and Notification", []),
        ("Testing Scenarios", ["acs", "tests"]),
        ("Additional Information and Attachments", []),
        ("Checklist", []),
    ],
    "csd": [
        ("Document Information and Purpose", []),
        ("Business Requirement Reference", ["rules"]),
        ("Related BPD and FSD References", ["fr_reference"]),
        ("Configuration Objectives and Scope", []),
        ("Prerequisites", []),
        ("Relevant SAP EWM Process Area", []),
        ("Configuration and Implementation Activities", ["cfg_activities"]),
        ("Configuration Decision Table", ["cfg_decisions"]),
        ("Required Configuration Values", ["cfg_values"]),
        ("Dependencies", []),
        ("Proposed Implementation Approach", []),
        ("Master Data and Process Prerequisites", []),
        ("Error Handling and Exception Behaviour", []),
        ("Testing and Validation", []),
        ("Transport and Deployment Considerations", []),
        ("Open Configuration Questions", ["open_items"]),
        ("Consultant Validation Checklist", ["checklist"]),
    ],
}

NAMES = {"bpd": ("Business Process Design", "BPD"), "fsd": ("Functional Specification Document", "FSD"),
         "csd": ("Configuration Specification Document", "CSD")}
PURPOSE = {
    "bpd": "Describes the business process, participants, sequence of activities, business rules and expected outcomes for BR-001.",
    "fsd": "Specifies the testable functional behaviour the system solution must implement for BR-001.",
    "csd": "Records the SAP EWM configuration and implementation activities, decisions and validations needed to support BR-001. No SAP system has been configured.",
}
ID_RE = re.compile(r"\b(?:DEC|OI|BPD|FR|AC|TS|CFG|BR)-\d{3}\b")
SAP_DETAIL_PATTERNS = [
    (re.compile(r"/SCWM/\w+", re.I), "SAP object name"),
    (re.compile(r"\b(?:SPRO|IMG)\s*(?:>|→|path\s*:)"), "IMG/SPRO path"),
    (re.compile(r"\b(?i:transaction(?: code)?|t-?code)\s+(?:/[A-Z]+/)?[A-Z]{2,}[A-Z0-9_]*\d[A-Z0-9_]*\b"), "transaction code"),
    (re.compile(r"\bBAdI\s+[A-Z/][A-Z0-9_/]{3,}"), "BAdI name"),
    (re.compile(r"\bmessage class\s+[A-Z/][A-Z0-9_/]+", re.I), "message class"),
    (re.compile(r"\btable\s+[A-Z/][A-Z0-9_/]{3,}\b"), "table name"),
]


def section_titles(doc_type):
    return [f"{i}. {title}" for i, (title, _) in enumerate(SECTIONS[doc_type], 1)]


def _blocks(name, model, plan):
    if name == "diagram":
        chart = bl.render_mermaid(plan)
        return f"```mermaid\n{chart}\n```" if chart else "Process flow diagram not available — no process steps defined."
    return {
        "steps": lambda: bl.render_process_steps(plan),
        "rules": lambda: bl.render_business_rules(model),
        "assumptions": lambda: bl.render_assumptions(model),
        "open_items": lambda: bl.render_open_items(model),
        "frs": lambda: bl.render_frs(plan),
        "acs": lambda: bl.render_acs(plan),
        "tests": lambda: bl.render_tests(plan),
        "fr_reference": lambda: bl.render_fr_reference(plan),
        "cfg_activities": lambda: bl.render_cfg_activities(plan),
        "cfg_decisions": lambda: bl.render_cfg_decisions(plan),
        "cfg_values": lambda: bl.render_cfg_values(plan),
        "checklist": lambda: bl.render_checklist(plan, model),
    }[name]()


def header(doc_type, model, plan, revision, model_name):
    name, short = NAMES[doc_type]
    active = active_open_items(model)
    answered = sum(1 for c in model["clarifications"] if c["status"] == "answered")
    status = f"DRAFT — {len(active)} open item(s), requires consultant review"
    today = datetime.date.today().isoformat()
    open_section = next(i for i, (t, b) in enumerate(SECTIONS[doc_type], 1) if "open_items" in b)
    lines = [
        f"# {name} ({short})",
        "",
        f"## 1. {SECTIONS[doc_type][0][0]}",
        "",
        bl.table(["Field", "Value"], [
            ("Document ID", f"SeeReq-{short}-{model['requirement_id']}"),
            ("Business requirement", model["requirement_id"]),
            ("Module", "SAP EWM"),
            ("Version", f"Draft 0.{revision}"),
            ("Status", status),
            ("Requirement baseline", plan["fingerprint"]),
            ("Prepared by", f"SeeReq — AI-assisted ({model_name} via Sarvam AI)"),
            ("Source language", model["language"].capitalize()),
        ]),
        "",
        "**Version history**",
        "",
        bl.table(["Version", "Date", "Change"], [
            (f"Draft 0.{revision}", today,
             f"{'Initial draft' if revision == 1 else 'Regenerated draft'} from the requirement and {answered} clarification answer(s)")]),
        "",
        f"**Purpose:** {PURPOSE[doc_type]}",
        "",
        f"> **Original requirement (verbatim):** {model['original_requirement'].replace(chr(10), ' ')}",
        ">",
        f"> **English interpretation:** {model['interpretation_en']}",
        "",
        "> **AI-assisted draft.** Generated from the captured requirement and clarification answers. It has not been "
        "approved by the business and has not been validated against an SAP system. Items marked PROPOSED, "
        "ASSUMPTION, OPEN or CONSULTANT VALIDATION REQUIRED need review.",
        "",
        f"**Open items:** {len(active)} — see section {open_section}.",
    ]
    return "\n".join(lines)


def _split_sections(body):
    """Return {section number: body text} from the model's markdown; duplicates keep the first occurrence."""
    sections, current, buf = {}, None, []
    for line in body.splitlines():
        m = re.match(r"^##\s+(\d+)[.)]?\s", line)
        if m:
            if current is not None and current not in sections:
                sections[current] = "\n".join(buf).strip()
            current, buf = int(m.group(1)), []
        elif current is not None:
            buf.append(line)
    if current is not None and current not in sections:
        sections[current] = "\n".join(buf).strip()
    return sections


def assemble(doc_type, body, model, plan, revision, model_name):
    """Build the final document. Returns (markdown, missing section titles)."""
    written = _split_sections(body)
    parts, missing = [header(doc_type, model, plan, revision, model_name)], []
    for i, (title, blocks) in enumerate(SECTIONS[doc_type], 1):
        if i == 1:
            continue
        text = re.sub(r"\{\{[A-Z_]+\}\}", "", written.get(i, "")).strip()
        if not text and not blocks:
            missing.append(f"{i}. {title}")
            text = "**Requires review** — this section was not generated. Regenerate the document or complete it manually."
        content = [_blocks(b, model, plan) for b in blocks] + ([text] if text else [])
        parts.append(f"## {i}. {title}\n\n" + "\n\n".join(content))
    parts.append("## Appendix A. Traceability Matrix\n\n" + bl.render_traceability(model, plan))
    return "\n\n".join(parts) + "\n", missing


# ---------------------------------------------------------------- checks

def _user_text(model):
    return " ".join([model["original_requirement"]] + [c["answer"] for c in model["clarifications"]])


def check_document(doc_type, markdown, model, plan):
    """Deterministic checks on one document. Returns a list of {level, doc, message}."""
    short = NAMES[doc_type][1]
    issues = []
    known = bl.known_ids(model, plan)
    unknown = sorted({i for i in ID_RE.findall(markdown) if i not in known})
    if unknown:
        issues.append({"level": "error", "doc": short, "message": f"References IDs that do not exist in the requirement baseline: {', '.join(unknown)}."})
    if model["original_requirement"].replace("\n", " ") not in markdown:
        issues.append({"level": "error", "doc": short, "message": "Does not quote the original requirement."})
    for title in section_titles(doc_type):
        if f"## {title}\n" not in markdown:
            issues.append({"level": "error", "doc": short, "message": f"Section '{title}' is missing."})
    if "**Requires review** — this section was not generated" in markdown:
        count = markdown.count("**Requires review** — this section was not generated")
        issues.append({"level": "warning", "doc": short, "message": f"{count} section(s) were not generated and are marked for review."})
    headings = re.findall(r"^##\s+.+$", markdown, re.M)
    dupes = sorted({h for h in headings if headings.count(h) > 1})
    if dupes:
        issues.append({"level": "warning", "doc": short, "message": f"Duplicate headings: {'; '.join(dupes)}."})
    open_ids = {o["id"] for o in active_open_items(model)}
    for line in markdown.splitlines():
        ids = set(ID_RE.findall(line)) & open_ids
        if ids and re.search(r"\bCONFIRMED\b", line) and not line.startswith("|"):
            issues.append({"level": "warning", "doc": short, "message": f"Open item {', '.join(sorted(ids))} appears next to a CONFIRMED label: “{line.strip()[:120]}”."})
    user = _user_text(model)
    for pattern, what in SAP_DETAIL_PATTERNS:
        for m in pattern.finditer(markdown):
            if m.group(0) not in user:
                issues.append({"level": "warning", "doc": short, "message": f"Possible unverified SAP detail ({what}): “{m.group(0)}”. Confirm with a consultant or remove."})
    expected = {"bpd": [s["id"] for s in plan["process_steps"]], "fsd": [f["id"] for f in plan["functional_requirements"]],
                "csd": [c["id"] for c in plan["config_activities"]]}[doc_type]
    absent = [i for i in expected if i not in markdown]
    if absent:
        issues.append({"level": "error", "doc": short, "message": f"Missing baseline items: {', '.join(absent)}."})
    return issues


def check_set(docs, model, plan):
    """Cross-document traceability check run before documents are presented as a set."""
    issues = []
    for doc_type, markdown in docs.items():
        if markdown:
            issues += check_document(doc_type, markdown, model, plan)
    present = {k for k, v in docs.items() if v}
    for doc_type in ("bpd", "fsd", "csd"):
        if doc_type not in present:
            issues.append({"level": "error", "doc": NAMES[doc_type][1], "message": "Document has not been generated."})
    prints = {k: (re.search(r"\| Requirement baseline \| (\w+) \|", v) or [None, None])[1] for k, v in docs.items() if v}
    if len(set(prints.values())) > 1 or any(p != plan["fingerprint"] for p in prints.values()):
        stale = [NAMES[k][1] for k, p in prints.items() if p != plan["fingerprint"]]
        issues.append({"level": "error", "doc": "All", "message": f"Documents were generated from different requirement baselines ({', '.join(stale) or 'mixed'}). Regenerate them."})
    counts = {k: (re.search(r"DRAFT — (\d+) open item", v) or [None, None])[1] for k, v in docs.items() if v}
    expected = str(len(active_open_items(model)))
    if any(c != expected for c in counts.values()):
        issues.append({"level": "error", "doc": "All", "message": f"Stated open-item counts {counts} do not match the {expected} active open items."})
    for w in plan.get("warnings", []):
        issues.append({"level": "warning", "doc": "Baseline", "message": w})
    status = "fail" if any(i["level"] == "error" for i in issues) else "warn" if issues else "pass"
    return {"status": status, "issues": issues, "fingerprint": plan["fingerprint"],
            "open_items": int(expected), "checked": sorted(NAMES[k][1] for k in present)}
