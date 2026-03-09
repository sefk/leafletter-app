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
- Write multi-line issue bodies to a temp file (e.g. /tmp/issueN-body.md) and pass with --body-file

## Issue Quality Standards

Every open issue should have:
- Clear, specific title
- Description with problem statement and recommended fix
- At least one label
- Milestone or backlog designation for substantial work

## Milestone Structure

(To be documented as milestones are created/reviewed)
