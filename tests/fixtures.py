"""Deterministic AI responses for the incorrect-product scenario.

Round 2 deliberately misbehaves the way a real model can: it omits a confirmed decision, tries to resolve an open item
with a skipped answer, promotes an inference to confirmed, duplicates an open item, re-asks an answered question and
skips extracting decisions from one answer. The merge rules must catch all of it.
"""

REQUIREMENT = "Jab warehouse mein galat product pick ho, toh system delivery complete nahi hone dena chahiye."

ROUND1 = {
    "interpretation_en": "When an incorrect product is picked, the system must prevent the delivery from being completed.",
    "business_objective": "Prevent incorrect products from progressing through outbound warehouse processing.",
    "process_area": "SAP EWM outbound processing — picking (requires consultant confirmation)",
    "expected_system_behavior": "The system prevents delivery completion when an incorrect product is picked.",
    "decisions": [
        {"ref": None, "description": "The delivery must not be completed when an incorrect product has been picked.",
         "source": "original_requirement", "source_ref": "BR-001", "status": "confirmed", "category": "Control"},
    ],
    "open_items": [
        {"ref": None, "description": "How an incorrect product is identified", "why_it_matters": "Defines the validation rule.",
         "owner": "business", "blocking": True, "status": "open"},
        {"ref": None, "description": "What the system does when an incorrect product is detected", "why_it_matters": "Defines the control.",
         "owner": "business", "blocking": True, "status": "open"},
        {"ref": None, "description": "How the picker reports an incorrect product", "why_it_matters": "Defines user interaction.",
         "owner": "business", "blocking": False, "status": "open"},
        {"ref": None, "description": "How repeated picking errors are handled", "why_it_matters": "Defines audit and people process.",
         "owner": "business", "blocking": False, "status": "open"},
        {"ref": None, "description": "What happens physically to the incorrectly picked product", "why_it_matters": "Inventory accuracy.",
         "owner": "business", "blocking": False, "status": "open"},
        {"ref": None, "description": "Whether a supervisor may override the block", "why_it_matters": "Defines authorisation.",
         "owner": "business", "blocking": False, "status": "open"},
        {"ref": None, "description": "Which SAP EWM mechanism enforces the validation", "why_it_matters": "Implementation approach.",
         "owner": "consultant", "blocking": False, "status": "open"},
    ],
    "clarification_questions": [
        {"question": "Galat product milne par system kya kare?", "question_en": "What should the system do when an incorrect product is detected?",
         "topic": "System action", "addresses": ["OI-002"]},
        {"question": "Picker galat product ko kaise report kare?", "question_en": "How should the picker report an incorrect product?",
         "topic": "Reporting", "addresses": ["OI-003"]},
        {"question": "Kaunsa EWM exit use karein?", "question_en": "Which EWM enhancement should enforce the check?",
         "topic": "Technical", "addresses": ["OI-007"]},
        {"question": "Baar baar galti ho toh kya kare?", "question_en": "What should happen when a picker repeatedly picks incorrectly?",
         "topic": "Repeated errors", "addresses": ["OI-004"]},
        {"question": "Galat product ka physically kya hoga?", "question_en": "What happens physically to the incorrectly picked product?",
         "topic": "Wrong item handling", "addresses": ["OI-005"]},
        {"question": "Kya supervisor block override kar sakta hai?", "question_en": "May a supervisor override the block?",
         "topic": "Override", "addresses": ["OI-006"]},
    ],
    "contradictions": [],
    "ready_for_documentation": False,
    "readiness_reason": "Key business decisions are missing.",
}

# Question IDs after round 1: Q1 action, Q2 reporting, Q3 repeated errors, Q4 wrong item, Q5 override.
ANSWERS = {
    "Q1": "Picking confirmation block ho aur error message dikhe. Warehouse task open rahe, cancel na ho. Zarurat ho toh supervisor task dusre picker ko reassign kar sake.",
    "Q2": "Ek dedicated 'Report Wrong Product' button ho. Negative quantity enter karne ki zarurat nahi honi chahiye.",
    "Q3": "Repeated errors record ho taaki supervisor review karke training de sake. Picker ko automatically block nahi karna.",
    "Q4": "Galat product discard nahi karna. Use designated return area mein rakho, warehouse staff verify kare aur original storage bin mein wapas rakhe. Handling record honi chahiye.",
    "Q5": "",
}

# Focused extraction of the round-2 answers. Q4 is deliberately missed to exercise the verbatim safety net.
EXTRACTION = {"decisions": [
    {"source_ref": "Q1", "description": "Picking confirmation must be blocked when the picked product is incorrect.", "category": "Validation"},
    {"source_ref": "Q1", "description": "An error message must be displayed to the picker when confirmation is blocked.", "category": "User interaction"},
    {"source_ref": "Q1", "description": "The warehouse task remains open after a failed confirmation and is not cancelled automatically.", "category": "Exception handling"},
    {"source_ref": "Q1", "description": "A supervisor may reassign the open task to another picker when necessary.", "category": "Roles"},
    {"source_ref": "Q2", "description": "A dedicated Report Wrong Product button must be available to report the issue.", "category": "User interaction"},
    {"source_ref": "Q2", "description": "Reporting an incorrect product must not require entering a negative quantity.", "category": "User interaction"},
    {"source_ref": "Q3", "description": "Repeated incorrect picks are recorded so a supervisor can review them and arrange training.", "category": "Audit"},
    {"source_ref": "Q3", "description": "A picker is not automatically blocked solely because of repeated errors.", "category": "Roles"},
    {"source_ref": "Q9", "description": "Ignored: refers to a question that was not asked.", "category": "Other"},
]}

ROUND2 = {
    "interpretation_en": "When an incorrect product is picked, picking confirmation is blocked and the exception is handled without completing the delivery.",
    "business_objective": "Prevent incorrect products from progressing through outbound warehouse processing.",
    "process_area": "SAP EWM outbound processing — picking (requires consultant confirmation)",
    "expected_system_behavior": "Block picking confirmation for an incorrect product, show an error and keep the task open.",
    "decisions": [  # DEC-001 deliberately omitted; the rest repeat the extraction and must not duplicate it
        {"ref": None, "description": "Picking confirmation must be blocked when the picked product is incorrect.", "source": "clarification", "source_ref": "Q1", "status": "confirmed", "category": "Validation"},
        {"ref": None, "description": "An error message must be displayed to the picker when confirmation is blocked.", "source": "clarification", "source_ref": "Q1", "status": "confirmed", "category": "User interaction"},
        {"ref": None, "description": "The warehouse task remains open after a failed confirmation and is not cancelled automatically.", "source": "clarification", "source_ref": "Q1", "status": "confirmed", "category": "Exception handling"},
        {"ref": None, "description": "A supervisor may reassign the open task to another picker when necessary.", "source": "clarification", "source_ref": "Q1", "status": "confirmed", "category": "Roles"},
        {"ref": None, "description": "A dedicated Report Wrong Product button must be available to report the issue.", "source": "clarification", "source_ref": "Q2", "status": "confirmed", "category": "User interaction"},
        {"ref": None, "description": "Reporting an incorrect product must not require entering a negative quantity.", "source": "clarification", "source_ref": "Q2", "status": "confirmed", "category": "User interaction"},
        {"ref": None, "description": "Repeated incorrect picks are recorded so a supervisor can review them and arrange training.", "source": "clarification", "source_ref": "Q3", "status": "confirmed", "category": "Audit"},
        {"ref": None, "description": "A picker is not automatically blocked solely because of repeated errors.", "source": "clarification", "source_ref": "Q3", "status": "confirmed", "category": "Roles"},
        {"ref": None, "description": "Pickers identify themselves with an operator ID on the RF device.", "source": "ai_inference", "source_ref": "", "status": "confirmed", "category": "Assumption"},
        {"ref": None, "description": "The supervisor may override the block.", "source": "clarification", "source_ref": "Q5", "status": "confirmed", "category": "Roles"},
        # Q4 deliberately not extracted
    ],
    "open_items": [
        {"ref": "OI-001", "description": "How an incorrect product is identified", "owner": "business", "blocking": True, "status": "open"},
        {"ref": "OI-002", "description": "What the system does when an incorrect product is detected", "status": "resolved", "resolved_by": "Q1"},
        {"ref": "OI-003", "description": "How the picker reports an incorrect product", "status": "resolved", "resolved_by": "Q2"},
        {"ref": "OI-004", "description": "How repeated picking errors are handled", "status": "resolved", "resolved_by": "Q3"},
        {"ref": "OI-005", "description": "What happens physically to the incorrectly picked product", "status": "resolved", "resolved_by": "Q4"},
        {"ref": "OI-006", "description": "Whether a supervisor may override the block", "status": "resolved", "resolved_by": "Q5"},
        {"ref": None, "description": "How is an incorrect product identified by the system", "owner": "business", "blocking": True, "status": "open"},
        {"ref": "OI-007", "description": "Which SAP EWM mechanism enforces the validation", "owner": "consultant", "status": "open"},
        {"ref": None, "description": "Who verifies items placed in the return area", "why_it_matters": "Defines responsibility for the return step.",
         "owner": "business", "blocking": False, "status": "open"},
    ],
    "clarification_questions": [
        {"question": "Picker galat product kaise report kare?", "question_en": "How should the picker report an incorrect product?", "topic": "Reporting", "addresses": ["OI-003"]},
        {"question": "Galat product ki pehchaan kaise ho — scan se?", "question_en": "How is an incorrect product identified — by scanning?", "topic": "Identification", "addresses": ["OI-001"]},
    ],
    "contradictions": [],
    "ready_for_documentation": True,
    "readiness_reason": "Main decisions are known.",
}

# Expected IDs after round 2:
# DEC-001 BR; DEC-002..009 from Q1-Q3; DEC-010 operator-ID inference (demoted); DEC-011 override (demoted, Q5 skipped);
# DEC-012 verbatim capture of Q4. Active open items: OI-001, OI-006, OI-007, OI-008.

BASELINE = {
    "process_steps": [
        {"id": "BPD-001", "kind": "start", "actor": "Picker", "action": "Picker starts picking the warehouse task", "next": [{"to": "BPD-002"}], "decision_refs": [], "status": "assumption"},
        {"id": "BPD-002", "kind": "task", "actor": "Picker", "action": "Picker picks the product and requests confirmation", "next": [{"to": "BPD-003"}], "decision_refs": ["DEC-001"], "status": "confirmed"},
        {"id": "BPD-003", "kind": "decision", "actor": "System", "action": "Is the picked product the expected product?",
         "next": [{"to": "BPD-004", "label": "Valid"}, {"to": "BPD-005", "label": "Invalid"}], "decision_refs": ["DEC-002"], "open_refs": ["OI-001"], "status": "confirmed"},
        {"id": "BPD-004", "kind": "task", "actor": "System", "action": "Confirm the pick", "next": [{"to": "BPD-010"}], "decision_refs": ["DEC-001"], "status": "confirmed"},
        {"id": "BPD-005", "kind": "task", "actor": "System", "action": "Block confirmation and show an error; task stays open", "next": [{"to": "BPD-006"}], "decision_refs": ["DEC-002", "DEC-003", "DEC-004"], "status": "confirmed"},
        {"id": "BPD-006", "kind": "task", "actor": "Picker", "action": "Report the issue with the Report Wrong Product button", "next": [{"to": "BPD-007"}], "decision_refs": ["DEC-006", "DEC-007", "DEC-008"], "status": "confirmed"},
        {"id": "BPD-007", "kind": "task", "actor": "Warehouse staff", "action": "Place item in return area, verify and return it to its original bin", "next": [{"to": "BPD-008"}], "decision_refs": ["DEC-012"], "open_refs": ["OI-008"], "status": "confirmed"},
        {"id": "BPD-008", "kind": "decision", "actor": "Supervisor", "action": "Can the current picker complete the task?",
         "next": [{"to": "BPD-002", "label": "Yes — retry"}, {"to": "BPD-009", "label": "No"}], "decision_refs": ["DEC-009"], "status": "confirmed"},
        {"id": "BPD-009", "kind": "task", "actor": "Supervisor", "action": "Reassign the task to another picker", "next": [{"to": "BPD-002"}], "decision_refs": ["DEC-005"], "status": "confirmed"},
        {"id": "BPD-010", "kind": "end", "actor": "System", "action": "Delivery item picked correctly", "next": [], "decision_refs": [], "status": "confirmed"},
    ],
    "functional_requirements": [
        {"id": "FR-001", "title": "Block delivery completion", "description": "The system shall prevent delivery completion while an incorrect pick exists.", "decision_refs": ["DEC-001"], "bpd_refs": ["BPD-004"], "business_status": "confirmed"},
        {"id": "FR-002", "title": "Block picking confirmation", "description": "The system shall reject picking confirmation when the product does not match.", "decision_refs": ["DEC-002"], "bpd_refs": ["BPD-003", "BPD-005"], "open_refs": ["OI-001"], "business_status": "confirmed"},
        {"id": "FR-003", "title": "Error message", "description": "The system shall display an error message stating the product is incorrect.", "decision_refs": ["DEC-003"], "bpd_refs": ["BPD-005"], "business_status": "confirmed"},
        {"id": "FR-004", "title": "Task stays open", "description": "The system shall keep the warehouse task open after a rejected confirmation.", "decision_refs": ["DEC-004"], "bpd_refs": ["BPD-005"], "business_status": "confirmed"},
        {"id": "FR-005", "title": "Supervisor reassignment", "description": "The system shall allow a supervisor to reassign the open task to another picker.", "decision_refs": ["DEC-005"], "bpd_refs": ["BPD-009"], "business_status": "confirmed"},
        {"id": "FR-006", "title": "Report Wrong Product", "description": "The system shall provide a Report Wrong Product button that does not require a negative quantity.", "decision_refs": ["DEC-006", "DEC-007"], "bpd_refs": ["BPD-006"], "business_status": "confirmed"},
        {"id": "FR-007", "title": "Record repeated errors", "description": "The system shall record each incorrect pick for supervisor review and shall not block the picker automatically.", "decision_refs": ["DEC-008", "DEC-009"], "bpd_refs": ["BPD-006", "BPD-008"], "business_status": "confirmed"},
        {"id": "FR-008", "title": "Return wrong item", "description": "The system shall support moving the item to a return area, verification and return to the original bin, and record the handling.", "decision_refs": ["DEC-012"], "bpd_refs": ["BPD-007"], "open_refs": ["OI-008"], "business_status": "confirmed"},
        {"id": "FR-009", "title": "Operator ID", "description": "The system shall capture the operator ID.", "decision_refs": ["DEC-010", "DEC-099"], "bpd_refs": ["BPD-002"], "business_status": "confirmed"},
    ],
    "acceptance_criteria": [
        {"id": f"AC-00{i}", "fr_refs": [f"FR-00{i}"], "given": "a picker processes a task", "when": "the condition occurs", "then": f"behaviour {i} is observed"}
        for i in range(1, 10)
    ],
    "test_scenarios": [
        {"id": f"TS-00{i}", "type": t, "scenario": f"Scenario {i}", "expected": "Expected", "fr_refs": [f"FR-00{i}"], "ac_refs": [f"AC-00{i}"]}
        for i, t in zip(range(1, 10), ["positive", "negative", "negative", "exception", "exception", "positive", "boundary", "exception", "positive"])
    ],
    "config_activities": [
        {"id": "CFG-001", "title": "Product validation during picking", "purpose": "Reject incorrect products", "fr_refs": ["FR-002", "FR-003"],
         "decision_needed": "Mechanism that validates the picked product", "approach_type": "unresolved", "investigate": "Standard validation vs enhancement",
         "verification": "Pick a wrong product and confirm rejection", "open_refs": ["OI-007"],
         "config_values": [{"setting": "Error message text", "value": "Wrong product", "status": "proposed"}]},
        {"id": "CFG-002", "title": "Report Wrong Product function", "purpose": "Reporting without negative quantity", "fr_refs": ["FR-006"],
         "decision_needed": "How the reporting function is provided", "approach_type": "possible_custom_development", "verification": "Report an incorrect product"},
        {"id": "CFG-003", "title": "Untraced activity", "purpose": "Should be flagged", "fr_refs": ["FR-999"], "approach_type": "standard_configuration"},
    ],
}


def doc_body(doc_type, extra=""):
    """A model-written body with every required section (sections 2..N)."""
    from seereq.documents import section_titles
    parts = [f"## {t}\n\nNarrative for {t} referencing FR-002 and DEC-005." for t in section_titles(doc_type)[1:]]
    return "\n\n".join(parts) + extra
