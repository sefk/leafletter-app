# Leafletter PM Agent Memory

## Label Taxonomy

Standard labels in sefk/leafletter-app:

| Label | Meaning |
|---|---|
| bug | Something isn't working |
| enhancement | New feature or request |
| documentation | Improvements or additions to documentation |
| good first issue | Good for newcomers |
| help wanted | Extra attention is needed |
| duplicate | Already exists |
| wontfix | Will not be worked on |
| P0 | Highest priority (exists, color #D93F0B) |
| P1 | High priority |
| P2 | Medium priority |
| P3 | Low priority |
| Performance | Performance-related issues |
| ux | User experience and usability |
| mobile | Mobile-specific UX issues |
| prod | Production incident or production-affecting issue (color #B60205, created 2026-03-07) |

## Shell Constraints

- Never use `$()` command substitution in gh or git commands
- Always hardcode repo as `sefk/leafletter-app` in all gh commands
- For multi-line issue bodies, write body to a temp file first and pass with `--body-file /tmp/body.md`, OR pipe via stdin. Do NOT use `$(cat <<'EOF'...)` — that is `$()` substitution.
- For `gh issue comment`, use `--body-file - <<'EOF'` (stdin heredoc) to avoid zsh warnings

## Known Production Incidents

- **2026-03-14 MySQL redo log exhaustion** (Issue #83): Railway default innodb_redo_log_capacity=100MB; OSM Street imports burst ~1.3GB/min causing write stalls. Immediate fix: raise to 1GB via MYSQL_INNODB_REDO_LOG_CAPACITY env var. Long-term fix tracked in #83: use bulk_create() with batch_size in Celery import task.

## Issue Quality Standards

Every open issue should have:
- Clear, specific title
- Description with problem statement and recommended fix
- At least one label
- Milestone or backlog designation for substantial work

## Board Audit History

- **2026-04-01**: Full audit — 34 open issues, all present on board. All closed issues correctly marked Done. No corrections needed; board was fully in sync.
- **2026-04-01 (session 2)**: 10 issues in Ready state. Grouped into 2 waves for parallel dev agents. Wave 1: #129, #130, #132, #104 (manage form); #103, #101 (map.js); #65 (OG tags). Wave 2: #134 (views.py + campaign_detail), #126 (hamburger menu). #136 held for design review.

## Codebase File Map (key files for agent assignment)

- `campaigns/templates/campaigns/manage/campaign_form.html` — manage new/update form
- `campaigns/templates/campaigns/manage/base.html` — manage base layout
- `campaigns/templates/campaigns/campaign_detail.html` — public worker view
- `campaigns/templates/campaigns/campaign_list.html` — public campaign list / home
- `campaigns/templates/campaigns/about.html` — about page
- `campaigns/static/campaigns/map.js` — all JS for map interaction (trip logging, worker view)
- `campaigns/views.py` — all views including publish logic
- `campaigns/forms.py` — Django forms for campaign management
- `campaigns/models.py` — Campaign, Street, Trip, CityFetchJob models

## Parallel Agent Rules (learned 2026-04-01)

- `campaign_form.html` (manage) and `map.js` are high-collision files — only one agent at a time
- `campaign_detail.html` is touched by many issues — sequence agents that touch it
- manage form issues (label/text changes) are safe to batch into one agent
- `views.py` changes (auth/publish logic) should be isolated from template-only agents
- Issue #136 (island-in-city, labeled large) requires design approval before implementation

## Django/GeoDjango Quirks (learned 2026-04-19)

- GeoDjango MySQL backend (`django.contrib.gis.db.backends.mysql`): `supports_update_conflicts=True` but `supports_update_conflicts_with_target=False` — cannot use `unique_fields` in `bulk_create`. Use `update_conflicts=True` without `unique_fields` (MySQL handles ON DUPLICATE KEY across all unique indexes). Fall back to `ignore_conflicts=True` for non-MySQL backends.
- Test DB is also MySQL (`test_leafletter`), not SQLite — check `connection.features` flags rather than vendor string when branching on DB capabilities.
- iOS xcodeproj is at `ios/Leafletter.xcodeproj` (NOT `ios/Leafletter/Leafletter.xcodeproj` as CLAUDE.md states). Available simulators: iPhone 17e, iPhone Air (OS 26.x).

## Worktree Dev Pattern

- Use `EnterWorktree` tool to create `.claude/worktrees/<name>/` on branch `worktree-<name>`
- RemoteTrigger does NOT support spawning sub-agents via prompt: schema requires `session_request.worker` but exact fields are unknown; `job_config` also fails. Cannot spawn parallel dev agents via RemoteTrigger.
- Cannot query GitHub Projects v2 board (Ready/Backlog/Done status) — PAT has only `repo` scope, not `read:project`. MCP plugin has no raw GraphQL tool.
- Workaround: infer Ready issues from issue characteristics (specific file refs, small scope, recent creation) and present plan for human to approve before making changes directly in this session.
- Verify Python syntax with `python -m py_compile <file>` (no Django setup needed)
- Leave changes unstaged/uncommitted; developer reviews via `git diff` in the worktree
- See [worktree-pattern.md](worktree-pattern.md) for full details

## Milestone Structure

(To be documented as milestones are created/reviewed)

## Project Board

- Board: "Leafletter dev", project number 1, owner sefk
- Project ID: `PVT_kwHOAAl7_c4BQfAR`
- Priority field ID: `PVTSSF_lAHOAAl7_c4BQfARzg-lv4s`
  - P0 option: `79628723`
  - P1 option: `0a877460`
  - P2 option: `da944a9c`
  - P3 option: `9f826784`
- Status field ID: `PVTSSF_lAHOAAl7_c4BQfARzg-lv24` (options: Backlog, Ready, Done)
- New issues ARE auto-added to the board via GHA workflow (merged in #117, 2026-03-29)
- Priority field is synced from labels by a separate workflow that fires on label changes
- Issues created before the auto-add workflow existed (#99–#116 range) may have been missing — audit found #101, #103, #104, #108, #109, #113, #115, #116 were missing and were added manually
- Issues with BLANK priority despite having P-labels are a known debt pattern; fix via `gh project item-edit --single-select-option-id`
