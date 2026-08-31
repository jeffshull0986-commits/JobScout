#!/usr/bin/env python3
"""
Local Job Scout dashboard server.

Pure standard library - no pip installs required. Serves the Kanban board
frontend and a small JSON REST API backed by dashboard/data/jobs.json.

Run:
    python3 server.py [port]

Then open http://localhost:8420 (or whatever port you passed).
"""
import json
import sys
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "jobs.json"
STATIC_DIR = ROOT / "static"

STAGES = [
    "review",
    "applied",
    "research_completed",
    "screening",
    "interview",
    "passed",
    "skipped",
]

# stage -> name of the date_* field that gets stamped when a card first
# enters that stage
STAGE_DATE_FIELD = {
    "applied": "date_applied",
    "research_completed": "date_research_completed",
    "screening": "date_screening",
    "interview": "date_interview",
    "passed": "date_passed",
}

_lock = threading.Lock()


def load_data():
    with _lock:
        if not DATA_FILE.exists():
            return {"next_id": 1, "jobs": []}
        return json.loads(DATA_FILE.read_text())


def save_data(data):
    with _lock:
        DATA_FILE.write_text(json.dumps(data, indent=2))


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message, status=400):
        self._send_json({"error": message}, status)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw)

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        file_path = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR not in file_path.parents and file_path != STATIC_DIR:
            self.send_response(403)
            self.end_headers()
            return
        if not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        content_type = {
            ".html": "text/html",
            ".js": "application/javascript",
            ".css": "text/css",
        }.get(file_path.suffix, "application/octet-stream")
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/jobs":
            self._send_json(load_data())
            return
        self._serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/jobs":
            try:
                payload = self._read_json_body()
            except json.JSONDecodeError:
                self._send_error_json("invalid JSON body")
                return
            data = load_data()
            job = {
                "id": data["next_id"],
                "title": payload.get("title", ""),
                "company": payload.get("company", ""),
                "location": payload.get("location", ""),
                "fit_score": payload.get("fit_score"),
                "score_reasoning": payload.get("score_reasoning", ""),
                "source": payload.get("source", ""),
                "job_url": payload.get("job_url", ""),
                "salary_range": payload.get("salary_range"),
                "stage": payload.get("stage", "review"),
                "date_added": payload.get("date_added") or date.today().isoformat(),
                "date_applied": None,
                "date_research_completed": None,
                "date_screening": None,
                "date_interview": None,
                "date_passed": None,
                "notes": payload.get("notes", ""),
                "research_notes": payload.get("research_notes", ""),
                "gmail_evidence": payload.get("gmail_evidence", []),
            }
            data["jobs"].append(job)
            data["next_id"] += 1
            save_data(data)
            self._send_json(job, status=201)
            return
        self._send_error_json("not found", status=404)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "jobs":
            try:
                job_id = int(parts[2])
            except ValueError:
                self._send_error_json("invalid job id")
                return
            try:
                payload = self._read_json_body()
            except json.JSONDecodeError:
                self._send_error_json("invalid JSON body")
                return

            data = load_data()
            job = next((j for j in data["jobs"] if j["id"] == job_id), None)
            if job is None:
                self._send_error_json("job not found", status=404)
                return

            new_stage = payload.get("stage")
            if new_stage and new_stage != job["stage"]:
                if new_stage not in STAGES:
                    self._send_error_json(f"unknown stage '{new_stage}'")
                    return
                job["stage"] = new_stage
                date_field = STAGE_DATE_FIELD.get(new_stage)
                if date_field and not job.get(date_field):
                    job[date_field] = date.today().isoformat()

            for field in (
                "title", "company", "location", "fit_score", "score_reasoning",
                "source", "job_url", "salary_range", "notes", "research_notes",
            ):
                if field in payload:
                    job[field] = payload[field]

            if "gmail_evidence" in payload:
                job["gmail_evidence"] = payload["gmail_evidence"]

            save_data(data)
            self._send_json(job)
            return
        self._send_error_json("not found", status=404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "jobs":
            try:
                job_id = int(parts[2])
            except ValueError:
                self._send_error_json("invalid job id")
                return
            data = load_data()
            before = len(data["jobs"])
            data["jobs"] = [j for j in data["jobs"] if j["id"] != job_id]
            if len(data["jobs"]) == before:
                self._send_error_json("job not found", status=404)
                return
            save_data(data)
            self._send_json({"ok": True})
            return
        self._send_error_json("not found", status=404)

    def log_message(self, format, *args):
        pass  # keep the console quiet


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8420
    server = ThreadingHTTPServer(("localhost", port), Handler)
    print(f"Job Scout dashboard running at http://localhost:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
