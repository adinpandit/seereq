# SeeReq — AI-powered SAP requirements analyst

Speak your business requirements in Hindi, Hinglish or English. Get structured SAP EWM documentation.

SeeReq captures a requirement (voice or text), analyzes it like a functional consultant, asks targeted clarification
questions in the user's language, and drafts three distinct English documents — BPD, FSD and CSD — for consultant review.

## Run

Requires Python 3.9+ and the Sarvam SDK (no other dependencies, no build step).

```bash
pip3 install sarvamai
```

Put your key in `.env` (already git-ignored):

```
SARVAM_API_KEY=your_key_here
```

Start the server and open http://127.0.0.1:8787

```bash
python3 server.py
```

Optional: `SEEREQ_PORT` (default `8787`) and `SEEREQ_HOST` (default `127.0.0.1`).

Without `SARVAM_API_KEY` the app still starts, shows a setup banner, and offers a clearly labelled **demo sample**
(captured from one run of the demonstration scenario) — it never presents demo data as a live analysis.

## How it works

| Step | Endpoint | What happens |
|---|---|---|
| Voice → text | `POST /api/transcribe` | Sarvam speech-to-text `saaras:v3` (`codemix` for Hindi/Hinglish, `en-IN` for English). |
| Analyze / clarify | `POST /api/analyze` | Answers are recorded; a focused call splits each new answer into atomic decisions; a second call updates the model. The server merges both with deterministic rules. |
| Requirement baseline | `POST /api/baseline` | One call drafts process steps (BPD), functional requirements (FR), acceptance criteria (AC), test scenarios (TS) and configuration activities (CFG); the server validates and renumbers them. |
| Documents | `POST /api/generate` | BPD, FSD and CSD are written from the same model + baseline. The AI writes narrative; the server owns section structure, headers and every ID table/diagram, checks the result and retries once. |
| Traceability check | `POST /api/check` | Cross-document checks before the set is presented. |

Chat model: `sarvam-105b-conversations` (max 8,192 output tokens). `sarvam-105b` was tried first but spent its budget reasoning.

### Canonical requirement model (single source of truth)

Kept in the browser, re-validated by the server on every request (`seereq/model.py`):

- **Decisions** `DEC-xxx` — description, source (original requirement / clarification answer / AI inference), source reference (`BR-001`, `Q3`), status (confirmed / proposed / assumption / superseded).
- **Open items** `OI-xxx` — description, why it matters, owner (business / consultant), blocking, status, resolving answer.
- **Clarifications** `Qn` — question, verbatim answer or *skipped*, open items it addresses.

Merge rules the AI cannot override:

- *Confirmed* only when sourced from BR-001 or a real (non-blank) answer; inferences become *assumption*.
- A confirmed decision is never dropped, downgraded or rewritten from a different source; it is superseded only by a later answer.
- Every answered question must be captured by a confirmed decision; if the AI misses one, the answer is kept verbatim.
- An open item closes only when a user answer resolves it; skipped questions confirm nothing, stay open and are not re-asked.
- Duplicate open items and repeated questions are dropped; questions about consultant-owned items are never put to business users.
- Readiness is computed by the app (no blocking business items), not taken from the AI.

### Documents and traceability

- BPD (13 sections), FSD (the 20-section structure) and CSD (17 sections) always contain every section; anything not generated is marked **Requires review**, never omitted.
- Decision register, open items, assumptions, process steps, FR, AC, TS, CFG tables, the Mermaid process-flow diagram and a traceability matrix are rendered by the application from the baseline, so IDs, statuses and open-item counts are identical across documents.
- Status vocabulary: CONFIRMED, PROPOSED, ASSUMPTION, OPEN, CONSULTANT VALIDATION REQUIRED. FRs carry business status and implementation status separately.
- The check flags unknown IDs, missing sections or baseline items, documents built from different baselines, mismatched open-item counts, open items labelled confirmed, untraced FR/CFG, uncovered decisions and possible invented SAP details (transaction codes, IMG paths, `/SCWM/` objects, BAdIs, tables).

## Tests

```bash
python3 -m unittest discover -s tests -v
```

38 tests with mocked AI responses (no API key needed): merge rules, baseline validation, document structure, traceability checks, repair retry and the HTTP API. The fixtures deliberately misbehave (drop a confirmed decision, resolve with a skipped answer, invent IDs) to prove the rules hold.

Live end-to-end check of the incorrect-product scenario (uses Sarvam credits):

```bash
python3 tests/live_scenario.py
```

## Notes and limitations

- Voice needs microphone access and `MediaRecorder`. Recordings are capped at 30 s; record again to append.
- Mermaid diagrams render in the preview via `cdn.jsdelivr.net` (mermaid 10.9.1); offline, the Mermaid source is shown. Exported Markdown keeps the ```` ```mermaid ```` block.
- The traceability check is deterministic: it verifies IDs, coverage and consistency, not the meaning of every sentence. Semantic contradictions in narrative text still need human review.
- One requirement (`BR-001`) per session. Export is Markdown (per document or combined); no Word export.
- Documents are AI-assisted drafts. SeeReq does not connect to or configure SAP.

`chat.py` is the original one-call Sarvam smoke test.
