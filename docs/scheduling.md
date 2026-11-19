# Scheduling the orchestrator

Todo Hub's daemon does the mechanical work on its own. The judgement work (reading your AI conversations, rewriting each project's `state` / `next`, deciding what to dispatch, writing the weekly review) is done by Claude, following [`CLAUDE.md`](../CLAUDE.md) (written in Chinese; section names are given below). Run it as two scheduled tasks in any Claude environment that can read and write this folder on your Mac (for example a Claude Code scheduled task, or Claude with the folder connected).

Suggested schedule, in your local time:

| Task | When | Prompt |
|---|---|---|
| Nightly update | every day, late evening | below |
| Weekly review | Sunday evening, before the nightly run | below |

## Nightly update prompt

```
Open the todohub folder, read CLAUDE.md (and CLAUDE.local.md if present) in full, then do the
「每晚更新」(nightly update) section exactly as written. Write only through the hub CLI. Finish with the
short report described there.
```

## Weekly review prompt

```
Open the todohub folder, read CLAUDE.md (and CLAUDE.local.md if present) in full, then do the
「周复盘」(weekly review) section exactly as written, including the nightly update it starts with.
Finish with the short report described there.
```

## Notes

- The orchestrator needs access to this folder only. It does not need your Reminders, mail or calendar; the daemon handles Reminders.
- If your environment cannot reach your past Claude conversations, the nightly run still works from local Codex / Claude Code sessions, git activity and todo changes (`hub digest`).
- Dispatch rules (what may run automatically) are in `CLAUDE.md` →「派单」(dispatch). Start with `dispatch.mode: "review"` in `config/hub.local.json` until you trust the setup.
