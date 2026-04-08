# Campaign Management Interface Rework

**Date:** 2026-04-06 (original proposal), updated 2026-04-08
**Scope:** `/manage/new/`, `/manage/<slug>/`, `/manage/<slug>/edit/`

---

## 1. Problem Statement

The campaign management interface had three core problems:

1. **Workflow opacity** -- Creating a campaign is a multi-step process (metadata, cities, boundary, review, publish) but the interface gave no indication of where you are or what comes next.

2. **Durability confusion** -- It wasn't clear which steps could be saved partway through. The city fetch background operation was explained, but nothing else was.

3. **Inconsistency** -- Descriptions were scattered (some above cards, some below), some appeared conditionally, and terminology varied ("Geographical Limit" vs. "Campaign Boundary").

Additionally, campaign editing was split across two disconnected pages: a read-only detail page and a separate edit form.

---

## 2. What We Built

### Phase 1: Progress Track Sidebar

Added a persistent sidebar to `/manage/<slug>/` that shows all 6 steps with completion state icons:

| Step | Completion condition |
|---|---|
| 1 Basics | `name` and `start_date` present |
| 2 Hero Image | `hero_image_effective_url` exists (optional step) |
| 3 Cities | At least one `CityFetchJob` with status `ready` |
| 4 Boundary | `campaign.geo_limit` is not null |
| 5 Review | Boundary set and `map_status` is `ready` or `warning` |
| 6 Publish | `campaign.status == 'published'` |

Step states are computed by `_get_step_states(campaign)` in `views.py` and rendered as colored circle icons: green check (complete), yellow filled (in-progress/available), yellow pulsing (async in-progress), orange (attention/error), gray (blocked/optional).

The sidebar is sticky-positioned and collapses to a horizontal strip on viewports under 760px.

### Phase 2: Consolidated Detail Page

Merged the separate edit page into the detail page:

- **Basics** -- inline `<form>` posting to `POST /manage/<slug>/save-basics/` with all metadata fields (name, dates, contact, instructions via Quill, test flag)
- **Cities** -- inline city search and add form posting to `POST /manage/<slug>/save-cities/`, with the existing per-city status table, re-fetch, and delete actions unchanged
- **Publish** -- dedicated card at the bottom with publish/unpublish button and public URL display

The `/manage/<slug>/edit/` URL now redirects to the detail page. Each section has its own form with a section-scoped save button, using POST-redirect-GET.

### Phase 3: Collapse/Expand -- Abandoned

We attempted wrapping each step section in `<details>` elements with server-controlled `open` attributes. This was reverted because it changed the page from a clean vertical flow to a less usable layout. The current approach -- all sections visible in a single scrollable column alongside the sidebar -- works well for demos and discovery.

### Campaign Creation: Interstitial Page

The original create flow used a full form with cities, slug, and other fields. We replaced it with a lightweight interstitial page at `/manage/new/` that collects only the campaign name:

- Slug is auto-generated server-side from the name (with collision handling: `summer-canvass`, `summer-canvass-2`, etc.)
- After creation, the user is redirected to `/manage/<slug>/` where all other fields are filled in via the inline step sections
- The `cities` model field defaults to `[]` so campaigns can be created without specifying cities upfront

We considered and rejected an inline form/modal on the campaign list page -- the list page auto-refreshes every 10 seconds during city imports, which would dismiss the form mid-typing.

### Terminology Changes

- "Geographical Limit" renamed to "Campaign Boundary" throughout the UI
- The sidebar labels use short names: Basics, Hero Image, Cities, Boundary, Review, Publish

### Bug Fix: Inflight Banner

Campaigns with no cities but `map_status='pending'` (the model default) were incorrectly shown as "import in progress." Both the list page inflight filter and the detail page banner now check that `campaign.cities` is non-empty before treating a campaign as in-flight.

---

## 3. What We Preserved

These elements were working well and were kept as-is:

- Per-city fetch status table with individual re-fetch and delete actions
- Pulsing dot + auto-polling for live status updates
- Leaflet-draw polygon editor for campaign boundary
- Badge system (`badge-ready`, `badge-generating`, etc.)
- "Open preview in new tab" link
- Quill rich text editor for instructions

---

## 4. Architecture

### URL Structure

| URL | Method | Purpose |
|---|---|---|
| `/manage/` | GET | Campaign list |
| `/manage/new/` | GET | Create page (name-only form) |
| `/manage/new/` | POST | Create campaign and redirect to detail |
| `/manage/<slug>/` | GET | Campaign detail with inline editing and sidebar |
| `/manage/<slug>/edit/` | GET/POST | Redirects to detail page |
| `/manage/<slug>/save-basics/` | POST | Save metadata fields |
| `/manage/<slug>/save-cities/` | POST | Add a city |

### Key Files

- `campaigns/views.py` -- `_get_step_states()`, `manage_campaign_quick_create`, `manage_save_basics`, `manage_save_cities`
- `campaigns/templates/campaigns/manage/campaign_detail.html` -- main manage template with sidebar + inline forms
- `campaigns/templates/campaigns/manage/campaign_create.html` -- lightweight create page
- `campaigns/templates/campaigns/manage/campaign_list.html` -- list page with link to create

---

## 5. Out of Scope / Future Work

- Promotion/sharing step after publish
- Mobile-optimized step track layout
- Campaign duplication / templates
- Slug editing (currently auto-generated and immutable)
- Collapse/expand for completed steps (attempted, abandoned -- could revisit with a different approach)
