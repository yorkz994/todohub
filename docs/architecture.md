# Architecture

Todo Hub is a few hundred lines per module, standard library only. This page is the map.

## Data layout

Everything lives under the repo folder (or `$HUB_ROOT`). Only code and example config are tracked by git; the rest is git-ignored.

| Path | Contents | Written by |
|---|---|---|
| `data/events/YYYY-MM/*.json` | one file per change, never edited or deleted | `hub` CLI, daemon, agents (all via `todohub.store`) |
| `data/repos.json` | registry of installed AI repos / tools | screener |
| `inbox/<source>/index.json` | summaries of Codex / Claude Code sessions, git activity, Reminders dump | collectors |
| `reviews/<week>.md`, `<week>.narrative.md` | weekly numbers / the orchestrator's written review | daemon / orchestrator |
| `swarm/runs/<task id>/` | prompt, output and log of each dispatched run | runner |
| `logs/daemon.log` | daemon log | daemon |
| `config/*.local.json`, `CLAUDE.local.md` | your personal settings | you |

## Event store

`todohub/store.py`. An event is `{"type", "ts", "actor", "data"}`. File names are a UTC timestamp with microseconds, the actor and a random suffix, and files are opened with exclusive create, so concurrent writers never collide. `fold()` replays all events into the current state in four phases (project definitions → merges → creations → everything else), so an event may refer to a project created later in the log.

| Type | Required | Other fields |
|---|---|---|
| `project.upsert` | `id` | `name`, `category`, `status` (active / backlog / paused / done / archived), `goal`, `state`, `next`, `summary`, `aliases`, `paths`, `add_paths` |
| `project.merge` | `into`, `from` | `threads` (old id → thread label) |
| `project.progress` | `project`, `text` | `thread`, `source`, `links`, `date` |
| `todo.add` | `title` | `id` (auto), `project`, `thread`, `due`, `priority` (1–3), `notes`, `source`, `rid`, `rlist` |
| `todo.update` / `todo.done` / `todo.reopen` / `todo.drop` | `id` | fields to change |
| `task.create` | `agent`, `title`, `prompt` | `id` (auto), `project`, `cwd`, `acceptance`, `todo` |
| `task.status` | `id`, `status` | `summary`, `run_dir` |
| `repo.note` | `key` | `status`, `note`, `tags`, `audit` |
| `session.assign` | `key`, `project` | |
| `review.published` | `week` | `path` |
| `meta.set` | `key`, `value` | |

`hub apply batch.json` validates a whole list first and writes nothing if any item is invalid.

## Daemon

`hub daemon` (started by launchd after `install.sh`) runs an HTTP server and two background loops.

Scheduler (intervals in `config/hub.json` → `intervals_min`):

| Job | Default | What |
|---|---|---|
| `collect` | 30 min | read new Codex / Claude Code sessions and git activity |
| `reminders` | 10 min | two-way sync with macOS Reminders (macOS only) |
| `screener` | 6 h | rescan installed repos and tools |
| `review` | 3 h | regenerate this week's numbers |
| `widget` | 2 min | relaunch the menu-bar app if it was closed by a crash |

Runner (every minute): picks queued tasks for CLI agents, respects `dispatch.mode`, the daily cap, the timeout and the pause switch, and records the result as `task.status`.

`hub restart` sets `meta.daemon_restart`; the daemon exits when no task is running and launchd starts it again with the new code.

## HTTP API (127.0.0.1 only)

| Method | Path | |
|---|---|---|
| GET | `/` | dashboard |
| GET | `/widget?mode=pop\|desk` | compact list for the menu-bar popover and desktop panel |
| GET | `/api/state` | full snapshot (projects, todos, tasks, repos, agents) |
| GET | `/api/timeline` | events grouped by day |
| GET | `/api/review?week=YYYY-Www` | weekly review |
| POST | `/api/event` | append one event (`{"type", "data"}`) |
| POST | `/api/job` | run a job now (`{"name": "collect"}`; also `reminders`, `screener`, `review`, `runner`) |

POST requests must send `X-Hub: 1` and a local `Host`, otherwise they are rejected.

## Agents

Defined in `config/agents.json` (+ `config/agents.local.json`):

- `cli` — the runner launches `cmd`, substituting `{prompt}`, `{cwd}`, `{out}`, `{model}` and `{root}`. Every prompt is wrapped in the contract in `todohub/runner.py`.
- `mailbox` — the agent has no CLI of its own but can run local commands; it pulls work with `hub task next --agent <name> --claim` and reports with `hub task result`.
- `orchestrator` — Claude, which reads `CLAUDE.md` and writes events; nothing is launched for it.
- `maintenance: true` — fixed local commands (restart, rebuild the widget, publish) that the orchestrator may queue; hidden from the dispatch menu.
