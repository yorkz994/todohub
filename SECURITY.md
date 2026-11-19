# Security

## Threat model

Todo Hub runs on your own Mac and can launch coding agents automatically. The main risks are:

1. **A web page or document tricking the daemon into running a task.** The HTTP server binds to `127.0.0.1` only; every write request must send the header `X-Hub: 1` and a `Host` of `127.0.0.1`/`localhost` (blocks cross-site requests and DNS rebinding).
2. **Prompt injection through collected content.** Conversations, logs, READMEs and web pages are treated as data. The orchestrator rules in `CLAUDE.md` and the agent contract in `todohub/runner.py` say so explicitly, and forbid dispatching anything that moves money, pushes or publishes, sends messages, deletes data or touches credentials.
3. **Runaway agents.** Dispatch has a daily cap (`dispatch.max_runs_per_day`), a per-run timeout, a pause switch (`hub pause`) and a review mode (`dispatch.mode: "review"`) where every task waits for `hub task approve`. Codex runs with `--sandbox workspace-write` by default.
4. **Leaking personal data when publishing.** Personal data lives in git-ignored folders and `.local` files; `scripts/publish.sh` refuses to commit unless `scripts/audit_public.py` passes.

If you turn on automatic dispatch, read the agent definitions in `config/agents.json` (and your `config/agents.local.json`) first: each `cmd` runs as your user.

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** (Security tab → "Report a vulnerability") rather than a public issue. Include steps to reproduce and the affected version or commit.
