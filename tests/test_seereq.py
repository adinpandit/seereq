"""SeeReq tests. AI calls are replaced with deterministic fixtures; no Sarvam API key is needed.

Run: python3 -m unittest discover -s tests -v
"""

import copy
import json
import os
import re
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("SARVAM_API_KEY", "test-key-not-used")

import fixtures as fx  # noqa: E402
from seereq import ai, baseline, documents  # noqa: E402
from seereq import model as rm  # noqa: E402


class FakeAI:
    """Replaces ai.chat_json / ai.chat_text with queued fixture responses."""

    def __init__(self, json_responses, text_fn=None):
        self.json_responses = list(json_responses)
        self.text_fn = text_fn or (lambda prompt: (fx.doc_body(self._doc_type(prompt)), False))
        self.prompts = []

    @staticmethod
    def _doc_type(prompt):
        return "bpd" if "BUSINESS PROCESS DESIGN" in prompt else "fsd" if "FUNCTIONAL SPECIFICATION" in prompt else "csd"

    def chat_json(self, prompt, max_tokens):
        self.prompts.append(prompt)
        return copy.deepcopy(self.json_responses.pop(0))

    def chat_text(self, prompt, max_tokens):
        self.prompts.append(prompt)
        return self.text_fn(prompt)

    def __enter__(self):
        self._saved = (ai.chat_json, ai.chat_text)
        ai.chat_json, ai.chat_text = self.chat_json, self.chat_text
        return self

    def __exit__(self, *exc):
        ai.chat_json, ai.chat_text = self._saved


def scenario_model():
    """Run both analysis rounds of the incorrect-product scenario through the real merge logic."""
    with FakeAI([fx.ROUND1, fx.EXTRACTION, fx.ROUND2]):
        m = ai.analyze(fx.REQUIREMENT, "hinglish", None, None)
        m = rm.normalize_model(json.loads(json.dumps(m)))  # round-trip through the browser
        m = ai.analyze(fx.REQUIREMENT, "hinglish", m, fx.ANSWERS)
    return m


def scenario_plan(m):
    with FakeAI([fx.BASELINE]):
        return ai.build_baseline(m)


def scenario_docs(m, plan):
    with FakeAI([]):
        return {t: ai.generate_document(t, m, plan)[0] for t in ("bpd", "fsd", "csd")}


def section(markdown, number):
    m = re.search(rf"^## {number}\. .*?(?=^## |\Z)", markdown, re.M | re.S)
    return m.group(0) if m else ""


# ---------------------------------------------------------------- canonical model

class ModelMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = scenario_model()
        cls.dec = {d["id"]: d for d in cls.m["decisions"]}

    def test_round1_questions_skip_consultant_items(self):
        with FakeAI([fx.ROUND1]):
            m = ai.analyze(fx.REQUIREMENT, "hinglish", None, None)
        ids = [q["id"] for q in m["pending_questions"]]
        self.assertEqual(ids, ["Q1", "Q2", "Q3", "Q4", "Q5"])
        self.assertFalse(any("enhancement" in q["question_en"] for q in m["pending_questions"]))

    def test_confirmed_decision_survives_when_ai_omits_it(self):
        self.assertEqual(self.dec["DEC-001"]["status"], "confirmed")
        self.assertEqual(self.dec["DEC-001"]["source_ref"], "BR-001")

    def test_every_answer_is_captured_by_a_confirmed_decision(self):
        cited = {d["source_ref"] for d in self.m["decisions"] if d["status"] == "confirmed"}
        for qid in ("Q1", "Q2", "Q3", "Q4"):
            self.assertIn(qid, cited)
        q4 = [d for d in self.m["decisions"] if d["source_ref"] == "Q4"]
        self.assertIn("return area", q4[0]["description"])  # verbatim safety net

    def test_all_scenario_decisions_present(self):
        text = " ".join(d["description"].lower() for d in self.m["decisions"] if d["status"] == "confirmed")
        for phrase in ("blocked", "error message", "remains open", "reassign", "report wrong product",
                       "negative quantity", "supervisor can review", "not automatically blocked", "return area",
                       "original storage bin", "handling record"):
            self.assertIn(phrase, text)

    def test_extraction_and_analysis_do_not_duplicate_decisions(self):
        confirmed = [d for d in self.m["decisions"] if d["status"] == "confirmed"]
        self.assertEqual(len(confirmed), 10)  # BR-001 + 8 extracted + Q4 verbatim
        self.assertFalse(any(d["source_ref"] == "Q9" for d in self.m["decisions"]))

    def test_confirmed_decision_keeps_its_source(self):
        m = copy.deepcopy(self.m)
        notes = []
        rm.merge_decisions(m, [{"ref": "DEC-001", "description": "Blocking picking confirmation prevents shipping wrong goods.",
                                "source": "clarification", "source_ref": "Q1", "status": "confirmed"}], notes)
        dec = {d["id"]: d for d in m["decisions"]}
        self.assertEqual(dec["DEC-001"]["source_ref"], "BR-001")
        self.assertEqual(dec["DEC-001"]["description"], self.dec["DEC-001"]["description"])
        self.assertEqual(len(m["decisions"]), len(self.m["decisions"]) + 1)

    def test_rerun_without_answers_keeps_pending_questions(self):
        with FakeAI([fx.ROUND1, fx.ROUND1]):
            m = ai.analyze(fx.REQUIREMENT, "hinglish", None, None)
            m = ai.analyze(fx.REQUIREMENT, "hinglish", m, None)
        self.assertEqual(m["clarifications"], [])
        self.assertEqual(m["round"], 0)

    def test_original_requirement_always_has_a_decision(self):
        r1 = copy.deepcopy(fx.ROUND1)
        r1["decisions"] = []
        with FakeAI([r1]):
            m = ai.analyze(fx.REQUIREMENT, "hinglish", None, None)
        self.assertEqual([(d["source_ref"], d["status"]) for d in m["decisions"]], [("BR-001", "confirmed")])

    def test_inference_cannot_be_confirmed(self):
        self.assertEqual(self.dec["DEC-010"]["status"], "assumption")

    def test_skipped_answer_confirms_nothing(self):
        self.assertEqual(self.dec["DEC-011"]["status"], "proposed")
        q5 = next(c for c in self.m["clarifications"] if c["id"] == "Q5")
        self.assertEqual(q5["status"], "skipped")

    def test_skipped_question_keeps_open_item_open(self):
        oi = {o["id"]: o for o in self.m["open_items"]}
        self.assertEqual(oi["OI-006"]["status"], "open")
        for i in ("OI-002", "OI-003", "OI-004", "OI-005"):
            self.assertEqual(oi[i]["status"], "resolved")

    def test_no_duplicate_open_items(self):
        active = [o["id"] for o in rm.active_open_items(self.m)]
        self.assertEqual(active, ["OI-001", "OI-006", "OI-007", "OI-008"])

    def test_repeated_question_not_reasked(self):
        self.assertEqual([q["id"] for q in self.m["pending_questions"]], ["Q6"])
        self.assertEqual(self.m["pending_questions"][0]["addresses"], ["OI-001"])

    def test_skipped_question_never_reasked(self):
        m = copy.deepcopy(self.m)
        raw = {"clarification_questions": [
            {"question": "x", "question_en": "May a supervisor override the block?", "addresses": ["OI-006"], "reask_reason": "It was skipped"},
            {"question": "y", "question_en": "Please tell us about overriding", "addresses": ["OI-006"]}]}
        rm.merge_analysis(m, raw)
        self.assertEqual(m["pending_questions"], [])
        self.assertEqual({o["id"]: o["status"] for o in m["open_items"]}["OI-006"], "open")

    def test_readiness_blocked_by_open_business_item(self):
        self.assertFalse(self.m["ready"])  # OI-001 is blocking even though the AI said ready

    def test_confirmed_decision_not_downgraded_or_withdrawn_without_answer(self):
        m = copy.deepcopy(self.m)
        raw = {"decisions": [{"ref": "DEC-002", "description": "x", "status": "assumption"},
                             {"ref": "DEC-003", "description": "y", "status": "superseded", "superseded_by": "Q5"}]}
        rm.merge_analysis(m, raw)
        dec = {d["id"]: d for d in m["decisions"]}
        self.assertEqual(dec["DEC-002"]["status"], "confirmed")
        self.assertEqual(dec["DEC-003"]["status"], "confirmed")  # Q5 was skipped

    def test_normalize_rejects_garbage(self):
        self.assertIsNone(rm.normalize_model({"original_requirement": ""}))
        m = rm.normalize_model({"original_requirement": "x", "decisions": [{"id": "BAD", "description": "y"}]})
        self.assertEqual(m["decisions"], [])


# ---------------------------------------------------------------- baseline

class BaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = scenario_model()
        cls.plan = scenario_plan(cls.m)

    def test_unknown_references_removed_and_flagged(self):
        fr9 = next(f for f in self.plan["functional_requirements"] if f["id"] == "FR-009")
        self.assertEqual(fr9["decision_refs"], ["DEC-010"])
        self.assertEqual(fr9["business_status"], "proposed")  # DEC-010 is an assumption
        self.assertTrue(any("DEC-099" in w for w in self.plan["warnings"]))

    def test_untraced_cfg_flagged_not_fabricated(self):
        cfg3 = next(c for c in self.plan["config_activities"] if c["id"] == "CFG-003")
        self.assertEqual(cfg3["fr_refs"], [])
        self.assertTrue(any("CFG-003" in w for w in self.plan["warnings"]))

    def test_unconfirmed_config_value_not_presented(self):
        value = self.plan["config_activities"][0]["config_values"][0]
        self.assertEqual(value["value"], "Not provided")

    def test_every_confirmed_decision_covered(self):
        self.assertEqual(self.plan["uncovered_decisions"], [])

    def test_mermaid_has_success_and_failure_paths(self):
        chart = baseline.render_mermaid(self.plan)
        self.assertTrue(chart.startswith("flowchart TD"))
        self.assertIn("BPD_003 -->|Valid| BPD_004", chart)
        self.assertIn("BPD_003 -->|Invalid| BPD_005", chart)
        self.assertIn("BPD_009 --> BPD_002", chart)
        self.assertNotIn('"?', chart.replace('?"', ""))

    def test_uncovered_decision_triggers_retry(self):
        thin = copy.deepcopy(fx.BASELINE)
        thin["functional_requirements"] = thin["functional_requirements"][:2]
        with FakeAI([thin, fx.BASELINE]) as fake:
            plan = ai.build_baseline(self.m)
        self.assertEqual(len(fake.prompts), 2)
        self.assertIn("did not cover", fake.prompts[1])
        self.assertEqual(plan["uncovered_decisions"], [])

    def test_revalidation_is_idempotent(self):
        again = baseline.validate_plan(json.loads(json.dumps(self.plan)), self.m)
        self.assertEqual(again["fingerprint"], self.plan["fingerprint"])
        self.assertEqual([f["id"] for f in again["functional_requirements"]],
                         [f["id"] for f in self.plan["functional_requirements"]])


# ---------------------------------------------------------------- documents and traceability

class DocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = scenario_model()
        cls.plan = scenario_plan(cls.m)
        cls.docs = scenario_docs(cls.m, cls.plan)

    def test_fsd_has_all_twenty_sections_in_order(self):
        headings = re.findall(r"^## (\d+)\. ", self.docs["fsd"], re.M)
        self.assertEqual(headings, [str(i) for i in range(1, 21)])
        self.assertIn("## 15. Currency and Units of Measure", self.docs["fsd"])

    def test_decisions_appear_in_the_right_sections(self):
        bpd, fsd, csd = self.docs["bpd"], self.docs["fsd"], self.docs["csd"]
        confirmed = [d["id"] for d in self.m["decisions"] if d["status"] == "confirmed"]
        for dec in confirmed:
            self.assertIn(dec, section(bpd, 9), f"{dec} missing from BPD business rules")
            self.assertIn(dec, section(fsd, 2), f"{dec} missing from FSD business requirement")
            self.assertIn(dec, section(csd, 2), f"{dec} missing from CSD requirement reference")
        self.assertIn("Report Wrong Product", section(fsd, 4))
        self.assertIn("reassign", section(fsd, 4))
        self.assertIn("return area", section(bpd, 8))
        self.assertIn("CFG-002", section(csd, 7))
        self.assertIn("FR-006", section(csd, 3))

    def test_open_items_consistent_across_documents(self):
        for doc in self.docs.values():
            self.assertIn("DRAFT — 4 open item(s)", doc)
            for oi in ("OI-001", "OI-006", "OI-007", "OI-008"):
                self.assertIn(oi, doc)
        self.assertIn("Resolved during clarification", section(self.docs["fsd"], 8))
        self.assertEqual(section(self.docs["fsd"], 8).count("| OI-006 |"), 1)

    def test_mermaid_diagram_embedded(self):
        self.assertIn("```mermaid\nflowchart TD", section(self.docs["bpd"], 8))
        self.assertIn("```mermaid\nflowchart TD", section(self.docs["fsd"], 5))

    def test_statuses_kept_distinct(self):
        fsd = self.docs["fsd"]
        self.assertIn("| FR-009 |", fsd)
        fr9_row = next(line for line in fsd.splitlines() if line.startswith("| FR-009 |"))
        self.assertIn("PROPOSED", fr9_row)
        fr2_row = next(line for line in fsd.splitlines() if line.startswith("| FR-002 |"))
        self.assertIn("CONFIRMED", fr2_row)
        self.assertIn("CONSULTANT VALIDATION REQUIRED", fr2_row)  # business-confirmed, technically unvalidated

    def test_traceability_check_passes_on_consistent_set(self):
        report = documents.check_set(self.docs, self.m, self.plan)
        errors = [i for i in report["issues"] if i["level"] == "error"]
        self.assertEqual(errors, [])
        self.assertEqual(report["open_items"], 4)

    def test_check_flags_invented_ids_and_sap_details(self):
        bad = fx.doc_body("csd", "\n\nUse transaction /SCWM/RFUI and FR-050 here.")
        markdown, _ = documents.assemble("csd", bad, self.m, self.plan, 1, "test")
        issues = documents.check_document("csd", markdown, self.m, self.plan)
        text = " ".join(i["message"] for i in issues)
        self.assertIn("FR-050", text)
        self.assertIn("/SCWM/RFUI", text)

    def test_mentioning_sap_terms_is_not_flagged(self):
        clean = fx.doc_body("csd", "\n\nNo transaction codes, IMG paths or table names are specified in this document.")
        markdown, _ = documents.assemble("csd", clean, self.m, self.plan, 1, "test")
        issues = documents.check_document("csd", markdown, self.m, self.plan)
        self.assertFalse(any("SAP detail" in i["message"] for i in issues))
        bad = fx.doc_body("csd", "\n\nUse transaction LT12 via SPRO > EWM > Goods Issue.")
        markdown, _ = documents.assemble("csd", bad, self.m, self.plan, 1, "test")
        text = " ".join(i["message"] for i in documents.check_document("csd", markdown, self.m, self.plan))
        self.assertIn("LT12", text)
        self.assertIn("SPRO >", text)

    def test_missing_section_is_marked_not_dropped(self):
        body = fx.doc_body("bpd").replace("## 11. Dependencies\n\nNarrative for 11. Dependencies referencing FR-002 and DEC-005.", "")
        markdown, missing = documents.assemble("bpd", body, self.m, self.plan, 1, "test")
        self.assertEqual(missing, ["11. Dependencies"])
        self.assertIn("## 11. Dependencies\n\n**Requires review**", markdown)

    def test_repair_attempt_on_invented_ids(self):
        calls = []

        def text_fn(prompt):
            calls.append(prompt)
            extra = " See FR-777." if len(calls) == 1 else ""
            return fx.doc_body("fsd", extra), False

        with FakeAI([], text_fn):
            markdown, issues = ai.generate_document("fsd", self.m, self.plan)
        self.assertEqual(len(calls), 2)
        self.assertIn("FR-777", calls[1])
        self.assertNotIn("FR-777", markdown)

    def test_stale_document_detected(self):
        docs = dict(self.docs)
        docs["csd"] = docs["csd"].replace(self.plan["fingerprint"], "deadbeef")
        report = documents.check_set(docs, self.m, self.plan)
        self.assertEqual(report["status"], "fail")


# ---------------------------------------------------------------- HTTP API (existing features)

class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_static_and_status(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"SeeReq", r.read())
        with urllib.request.urlopen(self.base + "/api/status") as r:
            self.assertTrue(json.load(r)["live"])
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + "/../.env")

    def test_full_flow_over_http(self):
        with FakeAI([fx.ROUND1, fx.EXTRACTION, fx.ROUND2, fx.BASELINE]):
            code, r1 = self.post("/api/analyze", {"requirement": fx.REQUIREMENT, "language": "hinglish"})
            self.assertEqual(code, 200)
            code, r2 = self.post("/api/analyze", {"requirement": fx.REQUIREMENT, "language": "hinglish",
                                                  "model": r1["model"], "answers": fx.ANSWERS})
            self.assertEqual(code, 200)
            code, b = self.post("/api/baseline", {"model": r2["model"]})
            self.assertEqual(code, 200)
            docs = {}
            for t in ("bpd", "fsd", "csd"):
                code, g = self.post("/api/generate", {"doc_type": t, "model": r2["model"], "plan": b["plan"], "revision": 1})
                self.assertEqual(code, 200)
                docs[t] = g["markdown"]
            code, c = self.post("/api/check", {"model": r2["model"], "plan": b["plan"], "docs": docs})
        self.assertEqual(code, 200)
        self.assertNotEqual(c["report"]["status"], "fail")

    def test_bad_requests(self):
        self.assertEqual(self.post("/api/analyze", {"requirement": " ", "language": "hinglish"})[0], 400)
        self.assertEqual(self.post("/api/analyze", {"requirement": "x", "language": "klingon"})[0], 400)
        self.assertEqual(self.post("/api/baseline", {})[0], 400)
        self.assertEqual(self.post("/api/generate", {"doc_type": "xyz"})[0], 400)
        self.assertEqual(self.post("/api/generate", {"doc_type": "bpd", "model": {"original_requirement": "x"}})[0], 400)


if __name__ == "__main__":
    unittest.main()
