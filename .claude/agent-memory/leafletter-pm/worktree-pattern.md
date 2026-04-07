---
name: Worktree dev workflow
description: How to spin up isolated worktrees for feature work and implement changes when RemoteTrigger agent spawning is unavailable
type: feedback
---

When the user asks to implement work in an isolated git worktree:
1. Use `EnterWorktree` tool with a descriptive name — this creates `.claude/worktrees/<name>/` on a new branch `worktree-<name>`
2. After entering, `pwd` confirms you are in the worktree; all edits go there
3. RemoteTrigger API requires `session_request` or `job_config` fields — schema not yet available; implement changes directly in the worktree instead of spawning a separate agent
4. Use `python -m py_compile <file>` to verify Python syntax without needing Django setup
5. Leave changes unstaged/untracked — do NOT commit; developer reviews via `git diff` in the worktree

**Why:** Developer wants to review before merging; worktree isolation prevents interference with `main`.

**How to apply:** Any time the user says "use a worktree" or "isolated worktree", use EnterWorktree, implement directly, and leave uncommitted.
