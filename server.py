"""Kivi — AI-powered SAP requirements analyst. Run: python3 server.py"""

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent
STATIC = ROOT / "static"

# Load .env into the environment (without overriding existing vars)
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

from kivi import ai  # noqa: E402  (needs the environment loaded first)

HOST = os.environ.get("KIVI_HOST", "127.0.0.1")
PORT = int(os.environ.get("KIVI_PORT", "8787"))
MAX_JSON_BYTES = 400_000
MAX_AUDIO_BYTES = 10_000_000
MAX_REQUIREMENT = 4000
MAX_ANSWER = 2000
MAX_TURNS = 40
MAX_RELATED_DOC = 60_000


class BadRequest(Exception):
    pass


def _language(value):
    if value not in ai.LANGUAGES:
        raise BadRequest("Unknown language.")
    return value


def _requirement(payload):
    text = payload.get("requirement")
    if not isinstance(text, str) or not text.strip():
        raise BadRequest("Please describe a requirement first.")
    if len(text) > MAX_REQUIREMENT:
        raise BadRequest(f"The requirement is too long (max {MAX_REQUIREMENT} characters).")
    return text.strip()


def _history(payload):
    history = payload.get("history") or []
    if not isinstance(history, list) or len(history) > MAX_TURNS:
        raise BadRequest("Invalid clarification history.")
    turns = []
    for t in history:
        if not isinstance(t, dict) or not isinstance(t.get("question_en"), str):
            raise BadRequest("Invalid clarification history.")
        answer = t.get("answer") if isinstance(t.get("answer"), str) else ""
        if len(answer) > MAX_ANSWER:
            raise BadRequest(f"An answer is too long (max {MAX_ANSWER} characters).")
        turns.append({
            "question_en": t["question_en"][:500],
            "topic": str(t.get("topic") or "")[:80],
            "answer": answer.strip(),
        })
    return turns


class Handler(BaseHTTPRequestHandler):
    server_version = "Kivi"

    def log_message(self, fmt, *args):
        # Request line and status only; never bodies or headers.
        print(f"{self.address_string()} {fmt % args}")

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self, limit):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise BadRequest("Empty request.")
        if length > limit:
            raise BadRequest("Request is too large.")
        return self.rfile.read(length)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            return self._send(200, {"live": ai.is_configured(), "chat_model": ai.CHAT_MODEL, "stt_model": ai.STT_MODEL})
        if path == "/":
            path = "/index.html"
        file = (STATIC / path.lstrip("/")).resolve()
        if not file.is_relative_to(STATIC.resolve()) or not file.is_file():
            return self._send(404, {"error": "Not found."})
        content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        self._send(200, file.read_bytes(), content_type)

    def do_POST(self):
        url = urlparse(self.path)
        try:
            if url.path == "/api/transcribe":
                language = _language(parse_qs(url.query).get("language", ["hinglish"])[0])
                audio = self._read_body(MAX_AUDIO_BYTES)
                mime = (self.headers.get("Content-Type") or "audio/webm").split(";")[0]
                if not mime.startswith("audio/"):
                    raise BadRequest("Expected an audio recording.")
                transcript = ai.transcribe(audio, mime, language)
                if not transcript:
                    raise ai.KiviError("No speech was detected. Please try recording again.", 422)
                return self._send(200, {"transcript": transcript})

            try:
                payload = json.loads(self._read_body(MAX_JSON_BYTES))
            except ValueError:
                raise BadRequest("Invalid JSON.")
            if not isinstance(payload, dict):
                raise BadRequest("Invalid request.")

            if url.path == "/api/analyze":
                requirement = _requirement(payload)
                language = _language(payload.get("language"))
                history = _history(payload)
                analysis = ai.analyze(requirement, language, history)
                return self._send(200, {"analysis": analysis, "status": ai.readiness(analysis, history)})

            if url.path == "/api/generate":
                doc_type = payload.get("doc_type")
                if doc_type not in ("bpd", "fsd", "csd"):
                    raise BadRequest("Unknown document type.")
                analysis = ai.normalize_analysis(payload.get("analysis"))
                if not analysis:
                    raise BadRequest("Analyze the requirement before generating documents.")
                related = payload.get("related") or {}
                related = {k: v[:MAX_RELATED_DOC] for k, v in related.items()
                           if k in ("bpd", "fsd", "csd") and k != doc_type and isinstance(v, str)}
                markdown, truncated = ai.generate_document(
                    doc_type, _requirement(payload), _language(payload.get("language")),
                    _history(payload), analysis, related)
                return self._send(200, {"markdown": markdown, "truncated": truncated})

            self._send(404, {"error": "Not found."})
        except BadRequest as e:
            self._send(400, {"error": str(e)})
        except ai.KiviError as e:
            self._send(e.status, {"error": str(e)})
        except Exception as e:  # never leak internals to the client
            print(f"Unexpected error on {url.path}: {type(e).__name__}")
            self._send(500, {"error": "Something went wrong on the server. Please try again."})


if __name__ == "__main__":
    status = "Sarvam AI connected" if ai.is_configured() else "SARVAM_API_KEY missing — demo mode only"
    print(f"Kivi running at http://{HOST}:{PORT}  ({status})")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
