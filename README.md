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

| Step | Where | Sarvam API |
|---|---|---|
| Voice capture (browser `MediaRecorder`, ≤30 s per recording) → transcription | `POST /api/transcribe` | Speech-to-text, `saaras:v3` (`codemix` mode for Hindi/Hinglish, `en-IN` for English) |
| Requirement analysis + clarification rounds (structured JSON, validated server-side) | `POST /api/analyze` | Chat completions, `sarvam-105b-conversations` |
| BPD → FSD → CSD, generated sequentially; each later document receives the earlier ones for consistent IDs | `POST /api/generate` | Chat completions, `sarvam-105b-conversations` |

- `server.py` — standard-library HTTP server: static files, request validation, error mapping. The API key stays server-side.
- `seereq/ai.py` — Sarvam calls, JSON parsing/validation, readiness check, document header (status, open items, AI-draft notice).
- `seereq/prompts.py` — all prompts; tune analysis and document quality here.
- `static/` — the UI (plain HTML/CSS/JS). Model output is rendered through an escaping Markdown renderer, never as raw HTML.
- Session state lives in browser `localStorage`, so a reload keeps the requirement, answers and documents.

`sarvam-105b` (the reasoning model) was tried first, but on these prompts it spent its whole token budget reasoning and
took 100 s+; the conversations variant returns valid output in ~3–7 s.

## Notes and limitations

- Voice needs a browser with microphone access and `MediaRecorder` (Chrome, Edge, Firefox, Safari). Recordings are capped at 30 s (Sarvam's synchronous STT limit); record again to append more.
- The readiness status combines the model's recommendation with an app-level check; generation is always allowed, and drafts list their open items.
- One requirement (`BR-001`) per session; **New requirement** clears it.
- Documents are AI-assisted drafts. SeeReq does not connect to or configure SAP, and does not invent transaction codes, IMG paths or tables — configuration items are labelled PROPOSED / UNKNOWN for consultant validation.
- Export is Markdown (per document or combined). No Word export.

`chat.py` is the original one-call Sarvam smoke test.
