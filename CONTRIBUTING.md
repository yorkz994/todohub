# Contributing

Thanks for helping. Issues and pull requests in English or Chinese are both fine.

## Ground rules

- **Standard library only.** Todo Hub has no third-party Python dependencies and should stay that way. Python 3.9 must keep working (it is what macOS's Command Line Tools ship).
- **All writes go through the event store.** New features add event types in `todohub/store.py` (`REQUIRED` + `fold()`), never edit existing event files.
- **No personal data in the repo.** Examples use made-up names. Anything specific to one person belongs in a `.local` file. CI runs `scripts/audit_public.py`; run it locally too.
- **Safety first for dispatch.** Changes that widen what agents may run automatically need a clear reason and a matching update to `CLAUDE.md` and `SECURITY.md`.

## Development

```bash
python3 tests/test_smoke.py            # end-to-end smoke test in a temp dir (also runs on Linux)
python3 tests/test_audit.py            # privacy audit tests
python3 scripts/demo.py                # dashboard with made-up data on :8799
python3 scripts/audit_public.py --history   # privacy check of files and commit history
bash widget/build.sh --no-launch       # compile the menu-bar app without starting it (macOS)
hub daemon                             # run the daemon in the foreground
```

Layout:

| Path | What |
|---|---|
| `todohub/store.py` | event store, validation, `fold()` |
| `todohub/cli.py` | `hub` command |
| `todohub/daemon.py` | HTTP server, scheduler, runner loop |
| `todohub/runner.py` | dispatch contract and CLI agent runner |
| `todohub/collectors/` | Codex / Claude Code session readers |
| `todohub/reminders.py`, `reminders.jxa.js` | Reminders sync (JXA via `osascript`) |
| `todohub/screener.py` | installed-repo registry |
| `todohub/web/` | dashboard and widget UI |
| `widget/` | native macOS menu-bar app |
| `scripts/` | demo data, privacy audit, publish helper, `gh` installer |

See [docs/architecture.md](docs/architecture.md) for the data model and daemon.

## Pull requests

- Keep them focused; describe what changed and how you tested it.
- Add or update checks in `tests/test_smoke.py` for behaviour changes.
- User-visible changes go in `CHANGELOG.md` under "Unreleased".

## Translations

UI strings are inline in `todohub/web/*.html` and CLI messages in Chinese. An i18n layer is welcome; please discuss the approach in an issue first.
