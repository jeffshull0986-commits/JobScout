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
- Moving a card into a stage for the first time stamps a `date_*` field
  (e.g. `date_applied`) automatically, so the history persists even if you
  drag it elsewhere later.

## Not yet wired up (Phase B)

This is the board itself — it does **not** yet auto-receive new jobs from
the daily job-scout routine or auto-advance stages from Gmail. That
routine runs in a cloud session with no direct network path to your
machine, so the plan is: the routine writes new/updated entries into this
same `data/jobs.json` and commits+pushes them to the repo, and this app
picks them up on your next `git pull`. Still need to settle exactly how/
when that push happens before wiring it in.
