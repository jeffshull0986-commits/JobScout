#!/usr/bin/env python3
"""
Local Job Scout dashboard server.

Pure standard library - no pip installs required. Serves the Kanban board
frontend and a small JSON REST API backed by dashboard/data/jobs.json.

Run the server:
    python3 server.py [port]
    Then open http://localhost:8420 (or whatever port you passed).

CLI mode (used by the job-scout routine to write to the board without a
server running - reads/writes dashboard/data/jobs.json directly):
    python3 server.py list-jobs [--stage STAGE]
    echo '{"title": "...", "company": "...", "stage": "review"}' | python3 server.py add-job
    python3 server.py advance-stage <id> <stage> [--evidence "text"]
    echo "research text" | python3 server.py set-research <id> [--stage research_completed]
    echo "contacts text" | python3 server.py set-contacts <id>
    python3 server.py sync   # pull, then commit+push jobs.json if changed
"""
import base64
import hmac
import json
import os
import subprocess
import sys
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
DATA_FILE = ROOT / "data" / "jobs.json"
DATA_FILE_REL = "dashboard/data/jobs.json"
STATIC_DIR = ROOT / "static"

# HTTP Basic Auth is opt-in: unset DASHBOARD_PASSWORD (the default, e.g.
# when running locally) and the whole app is open, exactly as before. Set
# it - typically as a hosted-deployment env var, never committed - to
# require a login for every route, API included.
AUTH_USER = os.environ.get("DASHBOARD_USER", "admin")
AUTH_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")

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


def create_job(data, payload):
    """Build a new job dict from payload, append it to data, and return it.

    Shared by the HTTP POST /api/jobs handler and the `add-job` CLI command
    (used by the job-scout routine) so both paths produce identically
    shaped records.
    """
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
        "networking_contacts": payload.get("networking_contacts", ""),
        "gmail_evidence": payload.get("gmail_evidence", []),
    }
    data["jobs"].append(job)
    data["next_id"] += 1
    return job


def apply_job_update(job, payload):
    """Mutate `job` in place from payload fields. Raises ValueError on a
    bad stage name. Shared by the HTTP PATCH handler and the
    `advance-stage` CLI command.
    """
    new_stage = payload.get("stage")
    if new_stage and new_stage != job["stage"]:
        if new_stage not in STAGES:
            raise ValueError(f"unknown stage '{new_stage}'")
        job["stage"] = new_stage
        date_field = STAGE_DATE_FIELD.get(new_stage)
        if date_field and not job.get(date_field):
            job[date_field] = date.today().isoformat()

    for field in (
        "title", "company", "location", "fit_score", "score_reasoning",
        "source", "job_url", "salary_range", "notes", "research_notes",
        "networking_contacts",
    ):
        if field in payload:
            job[field] = payload[field]

    if "gmail_evidence" in payload:
        job["gmail_evidence"] = payload["gmail_evidence"]
    if "add_gmail_evidence" in payload:
        job.setdefault("gmail_evidence", []).append(payload["add_gmail_evidence"])


def _run_git(*args):
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "git command timed out after 30s"
    except FileNotFoundError:
        return 1, "", "git executable not found on PATH"


def bootstrap_git_auth():
    """Give git push credentials to a hosted deployment (e.g. Render).

    Locally, git already has your own credentials configured, so this is a
    no-op there. On a host, set a GITHUB_TOKEN env var (a GitHub personal
    access token with repo scope) and this injects it into the `origin`
    remote URL on startup so sync_with_remote()'s `git push` can
    authenticate. Also sets a git identity (GIT_AUTHOR_NAME/EMAIL env vars,
    or a default) if the container doesn't have one.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return

    code, url, _ = _run_git("remote", "get-url", "origin")
    if code != 0 or not url or "@github.com" in url:
        return  # no remote, or it already has credentials embedded

    if url.startswith("https://github.com/"):
        path = url[len("https://github.com/"):]
    elif url.startswith("git@github.com:"):
        path = url[len("git@github.com:"):]
    else:
        return  # unrecognized remote format - leave it alone

    _run_git("remote", "set-url", "origin", f"https://x-access-token:{token}@github.com/{path}")
    _run_git("config", "user.email", os.environ.get("GIT_AUTHOR_EMAIL", "jobscout-bot@users.noreply.github.com"))
    _run_git("config", "user.name", os.environ.get("GIT_AUTHOR_NAME", "Job Scout Board"))


def ensure_on_branch():
    """Move off detached HEAD onto a real local branch, if needed.

    Hosted builders (Render included) check out a specific commit SHA
    rather than a branch tip, which leaves the clone in detached HEAD -
    sync_with_remote() then has no branch name to pull/push against. Set a
    GIT_BRANCH env var to override which branch to land on (default:
    main). No-op if already on a real branch, which covers local dev and
    any host that doesn't do this.
    """
    code, out, _ = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    if code == 0 and out and out != "HEAD":
        return

    branch = os.environ.get("GIT_BRANCH", "main")
    _run_git("fetch", "origin", branch)
    _run_git("checkout", "-B", branch, f"origin/{branch}")


def sync_with_remote():
    """Commit any local changes, then pull, then push.

    Operates on whatever branch is currently checked out (via its
    configured upstream), rather than a hardcoded branch name - this repo
    may be on a feature branch during development and on `main` once this
    is merged, and the sync logic should work correctly in both cases.

    Local changes are committed *before* pulling, not after - pulling into
    a dirty working tree makes git refuse to touch a file with uncommitted
    edits even when the incoming change wouldn't really conflict with it,
    which looks exactly like a merge conflict but isn't one. Committing
    first lets `git pull --no-rebase` do a real (usually clean) merge; a
    genuine conflict from concurrent edits to the same lines is still
    reported rather than auto-resolved, since silently picking a side on
    conflicting jobs.json content could throw away either the user's local
    edits or what the cloud routine wrote.

    Returns a dict describing what happened - never raises.
    """
    log = []

    code, out, err = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    if code != 0 or not out or out == "HEAD":
        return {
            "ok": False,
            "step": "branch",
            "message": "Could not determine current branch (detached HEAD?). "
                       "Check out a real branch (e.g. main) before syncing.",
            "log": [f"$ git rev-parse --abbrev-ref HEAD\n{out}\n{err}".strip()],
        }
    branch = out

    code, out, err = _run_git("status", "--porcelain", "--", DATA_FILE_REL)
    log.append(f"$ git status --porcelain -- {DATA_FILE_REL}\n{out}\n{err}".strip())
    has_local_changes = bool(out.strip())

    if has_local_changes:
        code, out, err = _run_git("add", DATA_FILE_REL)
        log.append(f"$ git add {DATA_FILE_REL}\n{out}\n{err}".strip())
        if code != 0:
            return {"ok": False, "step": "add", "message": err or "git add failed", "log": log}

        code, out, err = _run_git("commit", "-m", "Board sync from local UI")
        log.append(f"$ git commit -m 'Board sync from local UI'\n{out}\n{err}".strip())
        if code != 0:
            return {"ok": False, "step": "commit", "message": err or "git commit failed", "log": log}

    code, out, err = _run_git("pull", "--no-rebase", "origin", branch)
    log.append(f"$ git pull --no-rebase origin {branch}\n{out}\n{err}".strip())
    if code != 0:
        return {
            "ok": False,
            "step": "pull",
            "message": "Pull failed - most likely a merge conflict on jobs.json. "
                       "Resolve it manually in a terminal before syncing again.",
            "log": log,
        }

    if not has_local_changes:
        return {"ok": True, "message": "Already up to date, nothing to push.", "log": log}

    code, out, err = _run_git("push", "origin", branch)
    log.append(f"$ git push origin {branch}\n{out}\n{err}".strip())
    if code != 0:
        return {
            "ok": False,
            "step": "push",
            "message": "Committed locally but push failed - someone else may have "
                       "pushed in the meantime. Try syncing again.",
            "log": log,
        }

    return {"ok": True, "message": f"Synced - your changes are pushed to {branch}.", "log": log}


class Handler(BaseHTTPRequestHandler):
    def _authenticated(self):
        if not AUTH_PASSWORD:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            user, _, password = base64.b64decode(header[len("Basic "):]).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        return hmac.compare_digest(user, AUTH_USER) and hmac.compare_digest(password, AUTH_PASSWORD)

    def _send_auth_challenge(self):
        body = b"Authentication required"
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Job Scout Dashboard"')
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        if not self._authenticated():
            self._send_auth_challenge()
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/jobs":
            self._send_json(load_data())
            return
        self._serve_static(parsed.path)

    def do_POST(self):
        if not self._authenticated():
            self._send_auth_challenge()
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/sync":
            result = sync_with_remote()
            self._send_json(result, status=200 if result["ok"] else 409)
            return
        if parsed.path == "/api/jobs":
            try:
                payload = self._read_json_body()
            except json.JSONDecodeError:
                self._send_error_json("invalid JSON body")
                return
            data = load_data()
            job = create_job(data, payload)
            save_data(data)
            self._send_json(job, status=201)
            return
        self._send_error_json("not found", status=404)

    def do_PATCH(self):
        if not self._authenticated():
            self._send_auth_challenge()
            return
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

            try:
                apply_job_update(job, payload)
            except ValueError as e:
                self._send_error_json(str(e))
                return

            save_data(data)
            self._send_json(job)
            return
        self._send_error_json("not found", status=404)

    def do_DELETE(self):
        if not self._authenticated():
            self._send_auth_challenge()
            return
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


def _arg_value(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


def cmd_add_job():
    """Read a job payload as JSON on stdin, append it, print the saved job.

    Used by the job-scout routine to write new Review-stage cards without
    going through the (likely-not-running) local HTTP server.
    """
    payload = json.loads(sys.stdin.read())
    data = load_data()
    job = create_job(data, payload)
    save_data(data)
    print(json.dumps(job, indent=2))


def cmd_advance_stage(args):
    if len(args) < 2:
        print("usage: server.py advance-stage <id> <stage> [--evidence TEXT]", file=sys.stderr)
        sys.exit(1)
    job_id, new_stage = int(args[0]), args[1]
    evidence = _arg_value(args, "--evidence")

    data = load_data()
    job = next((j for j in data["jobs"] if j["id"] == job_id), None)
    if job is None:
        print(f"error: no job with id {job_id}", file=sys.stderr)
        sys.exit(1)

    payload = {"stage": new_stage}
    if evidence:
        payload["add_gmail_evidence"] = evidence
    try:
        apply_job_update(job, payload)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    save_data(data)
    print(json.dumps(job, indent=2))


def cmd_list_jobs(args):
    data = load_data()
    jobs = data["jobs"]
    stage = _arg_value(args, "--stage")
    if stage:
        jobs = [j for j in jobs if j["stage"] == stage]
    print(json.dumps(jobs, indent=2))


def cmd_set_research(args):
    if not args:
        print("usage: server.py set-research <id> [--stage STAGE] < research.txt", file=sys.stderr)
        sys.exit(1)
    job_id = int(args[0])
    research_text = sys.stdin.read()
    stage = _arg_value(args, "--stage")

    data = load_data()
    job = next((j for j in data["jobs"] if j["id"] == job_id), None)
    if job is None:
        print(f"error: no job with id {job_id}", file=sys.stderr)
        sys.exit(1)

    payload = {"research_notes": research_text}
    if stage:
        payload["stage"] = stage
    apply_job_update(job, payload)
    save_data(data)
    print(json.dumps(job, indent=2))


def cmd_set_contacts(args):
    if not args:
        print("usage: server.py set-contacts <id> < contacts.txt", file=sys.stderr)
        sys.exit(1)
    job_id = int(args[0])
    contacts_text = sys.stdin.read()

    data = load_data()
    job = next((j for j in data["jobs"] if j["id"] == job_id), None)
    if job is None:
        print(f"error: no job with id {job_id}", file=sys.stderr)
        sys.exit(1)

    apply_job_update(job, {"networking_contacts": contacts_text})
    save_data(data)
    print(json.dumps(job, indent=2))


def cmd_sync():
    result = sync_with_remote()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)


CLI_COMMANDS = {
    "add-job": lambda args: cmd_add_job(),
    "advance-stage": cmd_advance_stage,
    "list-jobs": cmd_list_jobs,
    "set-research": cmd_set_research,
    "set-contacts": cmd_set_contacts,
    "sync": lambda args: cmd_sync(),
}


def main():
    bootstrap_git_auth()
    ensure_on_branch()

    args = sys.argv[1:]
    if args and args[0] in CLI_COMMANDS:
        CLI_COMMANDS[args[0]](args[1:])
        return

    port = int(args[0]) if args else int(os.environ.get("PORT", 8420))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Job Scout dashboard running at http://0.0.0.0:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
