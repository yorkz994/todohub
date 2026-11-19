#!/usr/bin/env python3
"""Try Todo Hub with made-up data, without touching your own.

    python3 scripts/demo.py               # demo data in a temp folder, dashboard on http://127.0.0.1:8799
    python3 scripts/demo.py --port 9000
    python3 scripts/demo.py --dir /tmp/hub-demo --no-serve   # only write the demo data

Everything here is fictional. Nothing is synced to Reminders and no agent is run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", help="where to put the demo data (default: a new temp folder)")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--no-serve", action="store_true", help="write the data and exit")
    a = ap.parse_args()

    root = Path(a.dir) if a.dir else Path(tempfile.mkdtemp(prefix="todohub-demo-"))
    (root / "config").mkdir(parents=True, exist_ok=True)
    for name in ("hub.json", "agents.json", "taxonomy.json"):
        shutil.copy(SRC / "config" / name, root / "config" / name)
    json.dump({"owner_name": "Alex", "actor": "alex", "port": a.port, "reminders": {"enabled": False},
               "scan_roots": [], "dispatch": {"mode": "review"}},
              open(root / "config" / "hub.local.json", "w"), indent=1)
    os.environ["HUB_ROOT"] = str(root)
    os.environ.pop("HUB_PATH_MAP", None)
    sys.path.insert(0, str(SRC))

    from todohub import store, util  # noqa: E402  (HUB_ROOT must be set first)

    if not store.load_events():
        seed(store, util)
    print(f"Demo data: {root}")
    if a.no_serve:
        return
    from todohub import daemon  # noqa: E402
    daemon.main(jobs=False, port=a.port)


def seed(store, util) -> None:
    d0 = util.today()

    def day(n: int) -> str:
        return (d0 + dt.timedelta(days=n)).isoformat()

    ev = []

    def add(t, **data):
        ev.append({"type": t, "data": data})

    projects = [
        ("garden-planner", "Garden planner app", "projects", "active",
         "Ship a small app that tells me what to plant and when, for a 4 m² balcony.",
         "Planting calendar works for 12 crops; watering reminders are next. Two friends are testing it.",
         "Add watering reminders (me); collect tester feedback by Friday (testers)."),
        ("reading-notes", "Reading notes pipeline", "research", "active",
         "Turn highlights from books and papers into a searchable notes archive.",
         "Importer handles 3 sources; tagging is still manual and slow.",
         "Try automatic tagging on last month's notes; compare with my manual tags."),
        ("team-onboarding", "New-hire onboarding guide", "work", "active",
         "One guide that gets a new engineer to a first merged change in two days.",
         "Draft covers setup and first task; review with two recent hires pending.",
         "Book two 30-min reviews; fold feedback into v2."),
        ("home-lab", "Home lab", "ops", "active",
         "A quiet, low-power box that runs backups and a few local services.",
         "Backups run nightly; restore has not been tested yet.",
         "Run a full restore test into a scratch folder."),
        ("half-marathon", "Half marathon in spring", "personal", "active",
         "Finish a half marathon in under 2 hours.",
         "Week 6 of 16; long run at 14 km.",
         "Keep three runs a week; book the race."),
        ("conference-talk", "Conference talk", "work", "backlog",
         "Give a 20-minute talk on what we learned building internal tools.", "", ""),
    ]
    for pid, name, cat, status, goal, state, nxt in projects:
        add("project.upsert", id=pid, name=name, category=cat, status=status, goal=goal, state=state, next=nxt)

    progress = [
        ("garden-planner", "calendar", "Planting calendar covers 12 crops, with frost dates per region.", -1, "claude-chat"),
        ("garden-planner", "testing", "Two friends installed the beta; first bug report about units.", -3, "manual"),
        ("reading-notes", "importer", "Importer now reads e-reader highlights and PDF annotations.", -2, "codex"),
        ("team-onboarding", "draft", "Setup chapter rewritten; dropped three outdated steps.", -4, "claude-chat"),
        ("home-lab", "backups", "Nightly backups verified for a week; 2% disk growth per week.", -6, "manual"),
        ("half-marathon", "training", "Long run 14 km at an easy pace, no knee pain.", -2, "manual"),
    ]
    for pid, thread, text, n, src in progress:
        add("project.progress", project=pid, thread=thread, text=text, source=src, date=day(n))

    todos = [
        ("Add watering reminders", "garden-planner", "reminders", day(2), 1),
        ("Fix metric/imperial units in the calendar", "garden-planner", "testing", day(-1), 1),
        ("Collect tester feedback", "garden-planner", "testing", day(4), 2),
        ("Try automatic tagging on last month's notes", "reading-notes", "tagging", day(0), 2),
        ("Book review sessions with two recent hires", "team-onboarding", "review", day(1), 1),
        ("Run a full restore test", "home-lab", "backups", day(5), 2),
        ("Register for the spring race", "half-marathon", "race", day(7), 3),
        ("Write a talk outline", "conference-talk", "outline", None, 3),
    ]
    for title, pid, thread, due, prio in todos:
        data = {"title": title, "project": pid, "thread": thread, "priority": prio, "source": "demo"}
        if due:
            data["due"] = due
        add("todo.add", **data)
    store.apply_batch(ev)

    st = store.fold()
    done_id = next(t["id"] for t in st["todos"].values() if t["title"].startswith("Fix metric"))
    store.append("todo.done", {"id": done_id}, actor="alex")
    k1 = store.append("task.create", {"agent": "codex", "title": "Add unit tests for the planting calendar",
                                      "prompt": "Add tests covering frost dates and unit conversion.",
                                      "project": "garden-planner"}, actor="claude")["data"]["id"]
    store.append("task.status", {"id": k1, "status": "done",
                                 "summary": "Result: 18 tests added, all passing. Changes: tests/test_calendar.py. Open: none."},
                 actor="runner")
    store.append("task.create", {"agent": "claude-code", "title": "Draft importer for a new notes app",
                                 "prompt": "Read the export format and write an importer.",
                                 "project": "reading-notes"}, actor="claude")
    store.append("repo.note", {"key": "github.com/example/agent-kit", "status": "planned",
                               "note": "Want to try for local agents"}, actor="alex")


if __name__ == "__main__":
    main()
