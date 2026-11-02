"""Live end-to-end check of the incorrect-product scenario against the real Sarvam API.

Not part of the unit tests (it costs API credits and the model's wording varies). Run manually:
    python3 tests/live_scenario.py [output_dir]
"""

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for line in (ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []:
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from seereq import ai, documents  # noqa: E402
from seereq import model as rm  # noqa: E402

REQUIREMENT = "Jab warehouse mein galat product pick ho, toh system delivery complete nahi hone dena chahiye."
ANSWER_CHUNKS = [
    "Galat product par picking confirmation block ho aur error message dikhe. Failed confirmation ke baad warehouse task open rahe, cancel na ho.",
    "Zarurat ho toh supervisor task ko dusre picker ko reassign kar sake. Report karne ke liye ek dedicated 'Report Wrong Product' button ho; negative quantity enter karne ki zarurat nahi honi chahiye.",
    "Repeated errors record hone chahiye taaki supervisor review karke training arrange kar sake. Sirf repeated errors ki wajah se picker ko automatically block nahi karna.",
    "Galat product discard nahi karna. Use designated return area mein rakho, warehouse staff verify kare aur original storage bin mein wapas rakhe. Is handling ka record hona chahiye.",
]
# (label, regex that must match a confirmed decision, BPD section, FSD section)
EXPECTED = [
    ("Block confirmation", r"block", 9, 4),
    ("Error message", r"error message", 9, 4),
    ("Task remains open", r"(remain|stay)s? open|not (be )?cancel", 9, 4),
    ("Supervisor reassignment", r"reassign", 9, 4),
    ("Report button", r"button", 9, 4),
    ("No negative quantity", r"negative quantity", 9, 4),
    ("Repeated errors recorded", r"repeat|record", 9, 4),
    ("No automatic picker block", r"automatic", 9, 4),
    ("Return area & original bin", r"return area|original (storage )?bin", 9, 4),
    ("Handling recorded", r"handling|record", 9, 4),
]


def section(md, n):
    m = re.search(rf"^## {n}\. .*?(?=^## |\Z)", md, re.M | re.S)
    return m.group(0) if m else ""


def main(out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    m = ai.analyze(REQUIREMENT, "hinglish", None, None)
    print(f"Round 1: {len(m['pending_questions'])} questions, {len(rm.active_open_items(m))} open items ({time.time() - t0:.0f}s)")
    answers, chunks = {}, list(ANSWER_CHUNKS)
    for q in m["pending_questions"]:
        answers[q["id"]] = chunks.pop(0) if chunks else ""
    if chunks:  # fewer questions than chunks: append the rest to the last answer
        last = [k for k, v in answers.items() if v][-1]
        answers[last] += " " + " ".join(chunks)
    for q in m["pending_questions"]:
        print(f"  {q['id']} {q['question_en']}\n     -> {answers[q['id']] or '(skipped)'}")
    m = ai.analyze(REQUIREMENT, "hinglish", m, answers)
    skipped_q = [c["id"] for c in m["clarifications"] if c["status"] == "skipped"]
    print(f"Round 2: {len(m['decisions'])} decisions, {len(rm.active_open_items(m))} open items, "
          f"{len(m['pending_questions'])} new questions left unanswered, skipped={skipped_q}")
    for n in m["notes"]:
        print("  note:", n)

    plan = ai.build_baseline(m)
    print(f"Baseline: {len(plan['process_steps'])} steps, {len(plan['functional_requirements'])} FR, "
          f"{len(plan['acceptance_criteria'])} AC, {len(plan['test_scenarios'])} TS, {len(plan['config_activities'])} CFG, "
          f"uncovered={plan['uncovered_decisions']}")
    docs = {}
    for t in ("bpd", "fsd", "csd"):
        docs[t], issues = ai.generate_document(t, m, plan)
        (out_dir / f"SeeReq_{t.upper()}_BR-001.md").write_text(docs[t])
        print(f"{t.upper()}: {len(docs[t])} chars, {len(issues)} doc issue(s)")
    report = documents.check_set(docs, m, plan)
    (out_dir / "state.json").write_text(json.dumps({"model": m, "plan": plan, "docs": docs, "report": report}, ensure_ascii=False, indent=1))

    print("\nDecision coverage (decision → FR → BPD §9 / FSD §2+§4 / CSD via CFG):")
    confirmed = [d for d in m["decisions"] if d["status"] == "confirmed"]
    failures = 0
    for label, pattern, bpd_sec, fsd_sec in EXPECTED:
        decs = [d for d in confirmed if re.search(pattern, d["description"], re.I)]
        frs = [f for f in plan["functional_requirements"] if set(f["decision_refs"]) & {d["id"] for d in decs}]
        cfgs = [c["id"] for c in plan["config_activities"] if set(c["fr_refs"]) & {f["id"] for f in frs}]
        in_bpd = any(d["id"] in section(docs["bpd"], bpd_sec) for d in decs)
        in_fsd = any(f["id"] in section(docs["fsd"], fsd_sec) for f in frs)
        ok = bool(decs and frs and in_bpd and in_fsd)
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {label:28} DEC={[d['id'] for d in decs][:3]} FR={[f['id'] for f in frs][:3]} "
              f"BPD={in_bpd} FSD={in_fsd} CFG={cfgs[:3] or '—'}")
    active = rm.active_open_items(m)
    dupes = [(a["id"], b["id"]) for i, a in enumerate(active) for b in active[i + 1:] if rm.similar(a["description"], b["description"])]
    print(f"\nOpen items: {[o['id'] for o in active]}  duplicates={dupes or 'none'}")
    print(f"Unanswered round-2 questions still pending (not confirmed): {[q['id'] for q in m['pending_questions']]}")
    print(f"Traceability check: {report['status']} — {sum(i['level'] == 'error' for i in report['issues'])} error(s), "
          f"{sum(i['level'] == 'warning' for i in report['issues'])} warning(s)")
    for i in report["issues"]:
        print(f"  [{i['level']}] {i['doc']}: {i['message']}")
    print(f"\nTotal {time.time() - t0:.0f}s. Documents written to {out_dir}")
    return failures


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "live_output"
    sys.exit(1 if main(out) else 0)
