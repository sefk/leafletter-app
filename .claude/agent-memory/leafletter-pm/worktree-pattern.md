---
name: Worktree dev workflow
description: How to spin up isolated worktrees for feature work and implement changes when RemoteTrigger agent spawning is unavailable
type: feedback
---

When the user asks to implement work in an isolated git worktree:
1. Use `EnterWorktree` tool with a descriptive name — this creates `.claude/worktrees/<name>/` on a new branch `worktree-<name>`
2. After entering, `pwd` confirms you are in the worktree; all edits go there
3. RemoteTrigger API still cannot spawn parallel dev agents — explored fully 2026-04-19:
   - `session_request` requires `worker` field; `worker` requires unknown sub-fields (rejected as "Field required")
   - `job_config` requires "ccr" shape with `environment_id` — not available
   - Implement changes directly instead of trying to spawn sub-agents
4. Use `python -m py_compile <file>` to verify Python syntax without needing Django setup
5. Commit each issue separately with `closes #N` in the message

**Why:** RemoteTrigger sub-agent spawning is not accessible from PM agent context; implementing directly on main is the correct pattern when the user asks to dispatch dev work.

**How to apply:** Any time the user dispatches "dev agents" for Ready issues, implement directly in sequence on main, committing one issue at a time. Use parallel toolcalls for read-only research (fetching issue details, reading files) before beginning implementation.
