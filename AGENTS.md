# Todo Hub —— 给执行 agent（Codex / Claude Code / Grok Bot / 本地模型）的说明

这个文件夹是用户的个人项目 / Todo 系统的数据和代码。编排者是 Claude（手册见 CLAUDE.md）。
你是被派来做某一件具体的事，或者来汇报你和用户当天做了什么。

## 规则

- 写数据只用 `hub` 命令。不要手改或删除 `data/`、`inbox/`、`reviews/`、`swarm/` 下的任何文件。
- 先设署名：`export HUB_ACTOR=codex`（或 `claude-code` / `grokbot` / 你的名字）。
- 不要 git push、发布、发消息、下单或动资金；这些永远留给用户。
- 需要用户决定的事，写进结果总结，不要猜。

## 常用命令

```bash
hub status                                   # 一屏概览
hub project list                             # 项目 id 一览
hub todo list                                # 未完成的 todo
hub todo add "标题" -p <项目id> --thread <子线> --due 2026-11-30 --source grokbot
hub progress <项目id> "一两句事实" --source codex --thread <子线，如某个标的或某条 lead>
hub task next --agent grokbot --claim        # 取一件派给你的任务（没有则返回 {"none": true}）
hub task result <任务id> --status done --summary "结果：… 改动：… 待用户决定：…"
hub repos plan https://github.com/owner/repo --note "为什么想装"
```

`hub <命令> -h` 看全部参数。任务结果请按「结果 / 改动 / 未完成或待用户决定」三行写。
