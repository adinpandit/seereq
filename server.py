"""SeeReq — AI-powered SAP requirements analyst. Run: python3 server.py"""

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

from seereq import ai, baseline, documents  # noqa: E402  (needs the environment loaded first)
from seereq import model as rm  # noqa: E402

HOST = os.environ.get("SEEREQ_HOST", "127.0.0.1")
PORT = int(os.environ.get("SEEREQ_PORT", "8787"))
MAX_JSON_BYTES = 1_500_000
MAX_AUDIO_BYTES = 10_000_000
MAX_REQUIREMENT = 4000
MAX_ANSWER = 2000


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


def _model(payload, required=True):
    m = rm.normalize_model(payload.get("model"))
    if required and not m:
        raise BadRequest("Analyze the requirement first.")
    return m


def _plan(payload, m):
    raw = payload.get("plan")
    if not isinstance(raw, dict):
        raise BadRequest("Build the requirement baseline first.")
    return baseline.validate_plan(raw, m)  # never trust a client-supplied plan; re-validate it


class Handler(BaseHTTPRequestHandler):
    server_version = "SeeReq"

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
                    raise ai.SeeReqError("No speech was detected. Please try recording again.", 422)
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
                answers = payload.get("answers")  # None = re-run without submitting the current round
                if answers is not None:
                    valid = isinstance(answers, dict) and all(isinstance(v, str) and len(v) <= MAX_ANSWER
                                                              for v in answers.values())
                    if not valid:
                        raise BadRequest(f"Invalid answers (max {MAX_ANSWER} characters each).")
                m = ai.analyze(requirement, language, _model(payload, required=False), answers)
                return self._send(200, {"model": m})

            if url.path == "/api/baseline":
                m = _model(payload)
                if not rm.live_decisions(m):
                    raise BadRequest("No business decisions have been captured yet.")
                return self._send(200, {"plan": ai.build_baseline(m)})

            if url.path == "/api/generate":
                doc_type = payload.get("doc_type")
                if doc_type not in ("bpd", "fsd", "csd"):
                    raise BadRequest("Unknown document type.")
                m = _model(payload)
                revision = payload.get("revision") if isinstance(payload.get("revision"), int) else 1
                markdown, issues = ai.generate_document(doc_type, m, _plan(payload, m), max(1, min(revision, 99)))
                return self._send(200, {"markdown": markdown, "issues": issues})

            if url.path == "/api/check":
                m = _model(payload)
                docs = payload.get("docs") or {}
                docs = {k: v for k, v in docs.items() if k in ("bpd", "fsd", "csd") and isinstance(v, str)}
                return self._send(200, {"report": documents.check_set(docs, m, _plan(payload, m))})

            self._send(404, {"error": "Not found."})
        except BadRequest as e:
            self._send(400, {"error": str(e)})
        except ai.SeeReqError as e:
            self._send(e.status, {"error": str(e)})
        except Exception as e:  # never leak internals to the client
            print(f"Unexpected error on {url.path}: {type(e).__name__}")
            self._send(500, {"error": "Something went wrong on the server. Please try again."})


if __name__ == "__main__":
    status = "Sarvam AI connected" if ai.is_configured() else "SARVAM_API_KEY missing — demo mode only"
    print(f"SeeReq running at http://{HOST}:{PORT}  ({status})")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
