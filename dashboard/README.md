# Job Scout Board

A local Kanban board for tracking job applications, seeded from the daily
`/job-scout` routine. No installs required beyond Python 3 (stdlib only —
no pip, no Node).

## Run it

```
cd dashboard
python3 server.py
```

Then open http://localhost:8420 in your browser.

Pass a different port if 8420 is taken: `python3 server.py 8888`.

## How it works

- All data lives in `dashboard/data/jobs.json` — a plain JSON file, easy to
  read/diff/back up.
- The server (`server.py`) is a small REST API (`GET/POST /api/jobs`,
  `PATCH/DELETE /api/jobs/<id>`) plus a static file server for the frontend
  in `static/`.
- The board has 7 columns: Review, Applied, Research Completed, Screening,
  Interview, Passed, Skipped. Drag a card between columns to change its
  stage, or click a card to open/edit the full details.
- When a card hits Applied, the routine researches the company (marketing
  strategy, recent news) and searches LinkedIn for Director/C-level
  marketing people it would likely report to — both land in the card's
  Research Notes and Networking Contacts fields before it moves to
  Research Completed.
- Moving a card into a stage for the first time stamps a `date_*` field
  (e.g. `date_applied`) automatically, so the history persists even if you
  drag it elsewhere later.

## Staying in sync with the cloud routine

The daily job-scout routine runs in a cloud session with no direct network
path to your machine — the repo is the only thing connecting them. So:

- **The routine → you:** at the start of each run it pulls the latest
  `data/jobs.json`, writes new Review cards and any Gmail-driven stage
  moves, then commits and pushes straight to the branch it's running on
  (`main`, once this is merged) — no approval prompt, per your request.
  Your board picks that up next time you load the page after a `git
  pull`, or by clicking **Sync** in the UI.
- **You → the routine:** drag cards around, edit notes, whatever — then
  hit **Sync** (top right) to pull+commit+push your local changes back to
  the repo, so the next automated run sees them.

Both directions use the same underlying logic
(`server.py`'s `sync_with_remote()` — pull, then commit+push
`data/jobs.json` if it changed), whether triggered by the Sync button or
by the routine's own CLI call (`python3 server.py sync`).

**Conflict risk:** if you're mid-edit locally at the exact moment the
routine pushes, a `git pull` can hit a merge conflict on `jobs.json`. The
sync logic does not attempt to auto-resolve this — it reports the failure
and leaves your working copy alone. If that happens, resolve it manually
in a terminal (`git status` in the repo root will show the conflict).

## CLI (used by the routine, but you can run these too)

```
python3 server.py list-jobs [--stage STAGE]
python3 server.py needs-research
echo '{"title": "...", "company": "...", "stage": "review"}' | python3 server.py add-job
python3 server.py advance-stage <id> <stage> [--evidence "text"]
echo "research text" | python3 server.py set-research <id> [--stage research_completed]
echo "contacts text" | python3 server.py set-contacts <id>
python3 server.py sync
```

## Catching up research on demand

Research (company research + LinkedIn networking contacts) normally only
happens during a full job-scout run. If you've moved a card to Applied
outside of that — via drag-and-drop, say — and don't want to wait for the
next scheduled run, just ask: "research my applied jobs" (or similar).
That runs a lighter sweep — `needs-research` to find any `applied`-stage
card missing research and/or contacts, fills in just what's missing, and
pushes — without kicking off a whole new job search.
