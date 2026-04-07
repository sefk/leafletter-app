# UX Proposal: Rework of the Campaign Management Interface

**Prepared by:** UX Agent
**Date:** 2026-04-06
**Scope:** `/manage/<slug>/` (campaign detail) and `/manage/<slug>/edit/` (campaign form)

---

## 1. Current State Diagnosis

### 1.1 Structural Issues Found in the Code

Reading `campaign_detail.html` and `campaign_form.html` reveals the following concrete problems:

**The page is a vertical dump of five disconnected cards:**
1. "Campaign Details" — read-only summary of metadata
2. "Street Management" — import status with per-city table
3. "Geographical Limit" — inline map editor (only shown if `campaign.bbox` exists)
4. Stale-data warning card (shown only under specific conditions)
5. "Campaign Map" — the read-only preview map with trip legend

Between these cards the user can also navigate to `/manage/<slug>/edit/` which is a completely separate page containing a *sixth* area: the campaign form, which includes the city picker. There is no visual or semantic connection between the edit page and the detail page.

**What makes this confusing:**

- The detail page shows you campaign info but offers no inline editing — you edit via a separate page
- The geo limit editor lives on the detail page, but city selection lives on the edit page — two separate saves required, with no indication they are related
- The preview map at the bottom of the detail page appears to duplicate the public worker view, but its purpose (manager review vs. live view) is never stated
- The "Publish Campaign" button appears without context inside the Cities table section — no indication this is the terminal step of any workflow
- There is no visual indication of what step you are currently on, or what is blocking you from proceeding
- The `{% if campaign.bbox %}` guards mean significant UI sections simply don't exist until a prerequisite is met — no explanation offered for why they are missing

**The workflow the developer identified is actually implicit in five distinct server states:**

| State | Key indicator | Current UI signal |
|---|---|---|
| No streets yet | `map_status == 'pending'` with no cities | "Street Management" card shows empty table |
| Fetching in progress | `map_status in ('generating', 'rendering', 'pending')` | Animated pulsing dot, polling JS |
| Streets ready, no boundary | `map_status == 'ready'` and `not campaign.geo_limit` | Yellow warning card |
| Boundary drawn | `campaign.geo_limit` exists | Geo limit map shows polygon |
| Published | `campaign.status == 'published'` | Green URL box |

The interface makes you learn these states by accident. There is no top-level landmark saying "here is where you are."

---

## 2. Proposed Approach: Persistent Progress Track (Not a Wizard)

The developer correctly identified that paginated wizards create friction for demos and for users who want to jump between steps. The better solution is a **progress track**: a persistent, always-visible vertical sidebar (or top strip on narrow viewports) that shows all steps simultaneously, marks completion state for each, and acts as in-page navigation anchors. The content of each step occupies the main column and is always visible — nothing is hidden behind a "next" button.

This satisfies three goals simultaneously:
- First-time users see the complete roadmap immediately
- Returning users can glance at the track to see where they left off
- Power users and demoes can jump directly to any section

### 2.1 Page Layout Sketch

```
┌──────────────────────────────────────────────────────────────────────┐
│  Leafletter Manager                                   [hamburger]    │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ← Campaigns                           Summer Canvass 2026           │
│                                                                      │
│  ┌──────────────────┐  ┌──────────────────────────────────────────┐  │
│  │  SETUP STEPS     │  │                                          │  │
│  │                  │  │  [section content for selected/anchor]   │  │
│  │ ✓ 1 Basics       │  │                                          │  │
│  │ ✓ 2 Hero Image   │  │                                          │  │
│  │ ● 3 Cities       │  │                                          │  │
│  │   4 Boundary     │  │                                          │  │
│  │   5 Review       │  │                                          │  │
│  │   6 Publish      │  │                                          │  │
│  │                  │  │                                          │  │
│  │  Status:         │  │                                          │  │
│  │  ⏳ Fetching     │  │                                          │  │
│  │  streets…        │  │                                          │  │
│  └──────────────────┘  └──────────────────────────────────────────┘  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

On viewports under ~760px, the sidebar collapses into a horizontal step strip at the top of the content column, similar to a GitHub Actions run view.

### 2.2 Step States and Icons

Each step item in the track uses one of four states:

| State | Visual | Meaning |
|---|---|---|
| Complete | Green check circle | All required fields for this step are done |
| Current / In progress | Filled green circle (pulsing if async) | This step has content but is not fully done |
| Blocked | Gray circle | Cannot proceed until a prior step completes |
| Attention needed | Yellow warning circle | Completed but has an issue (e.g. fetch error, sparse addresses) |

The pulsing indicator already exists in the codebase (CSS keyframe `pulse` defined in `base.html`'s `badge-generating`). Reuse it.

---

## 3. Detailed Step Definitions

Below is the complete definition for each step, including what "complete" means, what content lives in the step section, and what the blocking dependency is.

### Step 1: Basics

**Completion condition:** `name`, `slug`, and `start_date` are non-empty
**Dependency:** None
**Section content:** Name, slug, dates, contact info, instructions (Quill editor), test campaign flag
**Note:** This is currently the `campaign_form.html` edit page. The proposal merges the basic metadata fields inline into the detail page as an editable section rather than a separate page.

**Save behavior:** All fields in this section save with a single "Save Basics" button inside the section. Auto-save on blur is ideal but out of scope; a section-scoped save button is sufficient. Inline validation, not full-page redirect.

### Step 2: Hero Image

**Completion condition:** `hero_image_effective_url` returns a value, OR manager has explicitly dismissed the step (hero image is optional)
**Dependency:** None (parallel with Step 1)
**Section content:** Current hero image preview or placeholder, "Paste URL" / "Upload File" tabs, attestation checkboxes
**Note:** Hero image is optional. The step track should reflect that with a softer "optional" label. If the manager has not added a hero image, the step shows as "Skipped (optional)" rather than blocking progress.

### Step 3: Cities

**Completion condition:** At least one city has `CityFetchJob.status == 'ready'`
**Dependency:** Step 1 (need a campaign to attach cities to)
**Section content:** City search input, selected city tags, per-city fetch status table, "Re-fetch All" button

**New copy for this section:**

> Choose the geographic area this campaign will cover. You can add entire cities or counties — counties are often easier since city boundaries sometimes exclude nearby neighborhoods.
>
> Streets are fetched from OpenStreetMap, which can take 5–15 minutes. You can leave this page and come back — fetching continues in the background.

**The current per-city status table already has the right structure.** No major rework needed — just better framing through the step track context.

### Step 4: Boundary

**Completion condition:** `campaign.geo_limit` is not null
**Dependency:** Step 3 must have at least one city with status `ready`
**Section content:** Geo limit map editor (the leaflet-draw polygon tool already implemented)

**This step currently suffers from two issues:**
1. The section is gated with `{% if campaign.bbox %}` which makes it simply invisible until Step 3 is done. A blocked user sees no explanation.
2. The map editor description says "Geographical Limit" which is jargon. Better: "Campaign Boundary."

**Proposed description when blocked:**

> Draw the exact boundary for this campaign. Leafletter will only show streets inside this area to your workers.
>
> _Waiting for street data to finish downloading before you can draw a boundary. This usually takes 5–15 minutes._

**Proposed description when ready:**

> Draw a polygon on the map to define the area workers will see. Streets outside the boundary are hidden from workers.
>
> After saving, the number of houses within the boundary will update automatically.

### Step 5: Review

**Completion condition:** Step 4 is complete (boundary is set and map has rendered)
**Dependency:** Step 4
**Section content:** The read-only "Campaign Map" preview + trip legend already implemented

**This step needs better labeling.** Currently the card says "Campaign Map" with no explanation of what it is. The manager may not realize this is exactly what workers see.

**Proposed label and intro copy:**

> **Worker Preview**
>
> This is what workers see when they open your campaign. Zoom in to verify the correct streets are included. You can switch between coverage views using the dropdown.
>
> [Coverage detail | Coverage summary | Hide coverage]
> [Open full preview →] (opens `/c/<slug>/` in new tab — the button already exists, just needs better prominence)

**The trip management table belongs here** (edit/delete individual trips) since it is part of reviewing the campaign state, not a setup step.

### Step 6: Publish

**Completion condition:** `campaign.status == 'published'`
**Dependency:** Step 5 (conceptually — publishing before review is the common mistake)
**Dependency is soft:** Do not technically block publishing. A campaign manager may want to publish before the boundary is perfect. But the step track makes the consequence visible: "Steps 4 and 5 are incomplete. Your workers will see an empty map."

**Section content:**

```
┌─────────────────────────────────────────────────────────────┐
│  Public URL                                                 │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  https://leafletter.app/c/summer-canvass-2026/       │   │
│  │  [Copy]  [Open ↗]                                    │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
│  Status: ● Draft — not visible to the public                │
│                                                             │
│  [ Publish Campaign ]                                       │
│                                                             │
│  When published, anyone with this URL can log leafletting   │
│  trips. You can unpublish at any time.                      │
└─────────────────────────────────────────────────────────────┘
```

If published, the section flips to show "Published" state with the Unpublish button. This is already implemented — just needs relocation into the step framework.

---

## 4. Durability Semantics (Addressing Problem 2)

The fundamental confusion is that different sections have different save semantics and none of them are explained. The proposal standardizes this with two explicit save models:

### Model A: Explicit save (most fields)

Steps 1 and 2 use a "Save [Section Name]" button within the section. After saving, a brief inline success message appears ("Saved"). No full-page redirect. The user stays in place.

### Model B: Auto-save with feedback (geo operations)

The boundary tool (Step 4) already saves via `fetch()` AJAX call when the user clicks "Save Geographical Limit." This is fine. After saving, show a persistent inline note: "Boundary saved. Streets outside this area are now hidden from workers."

### Model C: Background operation (city fetch)

Step 3 is the only genuinely async operation. The copy should be more explicit:

> Streets are being downloaded in the background. **You don't need to stay on this page.** The step will update automatically when complete. You can safely close this tab and come back later.

Currently this information exists in the code but is buried in paragraph text inside the card. Moving it into a clearly labeled callout box under the step title makes it scannable.

---

## 5. Consolidating the Two-Page Problem (campaign_detail + campaign_form)

Currently the user workflow bounces between:
- `/manage/<slug>/` (read-only overview + map)
- `/manage/<slug>/edit/` (metadata form + city picker)

This split is unintuitive. The detail page links to the edit page and back, with no clear reason why they are separate.

**The proposal consolidates into one page.** All editable sections appear on `/manage/<slug>/` inline. The `/manage/<slug>/edit/` route would either redirect or remain as a form-only fallback (useful if someone bookmarks it), but it would no longer be the primary editing surface.

**Implementation notes for the developer:**
- Each section section can be its own `<form>` element with its own action endpoint (e.g., `POST /manage/<slug>/save-basics/`, `POST /manage/<slug>/save-hero/`)
- This avoids a large monolithic form with complex validation interactions
- The Quill rich text editor needs some care on the inline model — the hidden `instructions` field must be scoped to its form, not the outer page
- Section-scoped forms also prevent the current footgun: adding cities and forgetting to scroll past "Save" to save metadata

---

## 6. Handling the "Manage" Link Visibility (Pre-existing Issue)

From prior review notes (ux-review-initial.md): the "Manage" link is visible to all workers in the campaign detail header. This is a separate issue from the current proposal but is worth noting here because the proposed consolidation to a single manage page makes the URL structure slightly more visible to workers who look at network requests. The underlying security model should be revisited independently of this UX rework.

---

## 7. ASCII Wireframe: Full Single-Page Layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Leafletter Manager                                      ≡ (hamburger)   │
├──────────────────────────────────────────────────────────────────────────┤
│ ← All Campaigns                                                          │
│                                                                          │
│ ╔══════════════════════════════════════════════════════════════════════╗ │
│ ║  Summer Canvass 2026              [Draft]   [Open preview ↗]         ║ │
│ ╚══════════════════════════════════════════════════════════════════════╝ │
│                                                                          │
│  ┌─────────────────┐  ┌────────────────────────────────────────────┐     │
│  │ Campaign Setup  │  │                                            │     │
│  │                 │  │  ▼ Step 1: Basics                 [Edit]   │     │
│  │ ✓ 1  Basics     │  │  ┌──────────────────────────────────────┐  │     │
│  │ ○ 2  Hero Image │  │  │  Name: Summer Canvass 2026           │  │     │
│  │ ● 3  Cities     │  │  │  Dates: Jun 1 – Jul 31, 2026         │  │     │
│  │     ⏳ fetching │  │  │  Contact: jane@example.org           │  │     │
│  │ ○ 4  Boundary   │  │  └──────────────────────────────────────┘  │     │
│  │ ○ 5  Review     │  │                                            │     │
│  │ ○ 6  Publish    │  │  ▼ Step 2: Hero Image             [Edit]   │     │
│  └─────────────────┘  │  ┌──────────────────────────────────────┐  │     │
│                       │  │  [no hero image set — optional]      │  │     │
│                       │  └──────────────────────────────────────┘  │     │
│                       │                                            │     │
│                       │  ▼ Step 3: Cities                          │     │
│                       │  ┌──────────────────────────────────────┐  │     │
│                       │  │ ● Oakland, CA      ⏳ Downloading…   │  │     │
│                       │  │ ✓ Berkeley, CA     2,341 blocks      │  │     │
│                       │  │ [+ Add city or county]               │  │     │
│                       │  │                                      │  │     │
│                       │  │ Streets are downloading in the       │  │     │
│                       │  │ background. You can leave this page. │  │     │
│                       │  └──────────────────────────────────────┘  │     │
│                       │                                            │     │
│                       │  ▷ Step 4: Boundary    (blocked)           │     │
│                       │  Waiting for streets to finish…            │     │
│                       │                                            │     │
│                       │  ▷ Step 5: Review      (blocked)           │     │
│                       │  ▷ Step 6: Publish     (blocked)           │     │
│                       └────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────┘
```

Completed steps collapse to a header row with an [Edit] affordance that expands them inline. In-progress or incomplete steps are fully expanded. This makes the page shorter by default while keeping everything reachable.

### Collapse / expand behavior

- Steps 1 and 2 collapse once their completion condition is met (manager has saved basics; hero image is set or skipped)
- Steps 3, 4, 5, and 6 remain expanded when they are the current active step
- All steps can be manually expanded/collapsed by clicking the header, at any time
- This is trivially implemented with a `<details>` element and summary, no JS required for the basic behavior. The disclosure triangle is familiar to managers.

```html
<details open id="step-3-cities">
  <summary class="step-header">
    <span class="step-icon step-in-progress">●</span>
    Step 3: Cities
  </summary>
  <div class="step-body">
    <!-- city picker content here -->
  </div>
</details>
```

Server-side rendering determines the `open` attribute on each `<details>` element based on the current campaign state. No JS needed for initial state. The polling JS that already exists continues to work for live updates within the expanded Step 3.

---

## 8. Specific Copy Improvements

### Problem 3: Inconsistent description placement

| Current text location | Current text | Proposed change |
|---|---|---|
| Above Street Management card | "Streets are fetched from OpenStreetMap…fetching can take a long time…tries six times before giving up" | Move to Step 3 section intro. Shorten: "Streets are downloaded from OpenStreetMap — usually 5–15 minutes, sometimes longer." |
| In-flight banner (appears mid-card) | "Street import in progress — status updates automatically. This may take 10 minutes or more…" | Relocate to Step 3 callout box. Always visible, not conditionally injected. |
| Below geo limit map | "This view matches the Geographical Limit selected above and matches what is shown to workers." | Replace with "Worker Preview — this is exactly what workers see." Move to Step 5 header. |
| Campaign Details card > Public URL | Draft URL shown in pink with warning | Move to Step 6 section. Context there makes it less alarming. |

### Terminology changes

- "Geographical Limit" → "Campaign Boundary" (more natural; "limit" implies restriction)
- "Street Management" → removed as a standalone heading; replaced by "Cities" step label
- "blocks" in the per-city table → "street segments" or keep "blocks" but add footnote once, not on every page
- "Re-fetch All Streets" → "Re-download Streets" (fetch is developer jargon)

---

## 9. What NOT to Change

These elements of the current implementation are working well and should be preserved:

- The per-city fetch status table with individual re-fetch and delete actions is excellent — keeps managers in control at a granular level
- The pulsing dot + auto-polling pattern for live status updates is well-implemented
- The `bbox-map` geo limit editor with spacebar panning is clever and functional
- The hex color badge system (`badge-ready`, `badge-generating`, etc.) is clear and consistent
- The "open preview in new tab" link is the right affordance; just needs more prominence
- The Quill rich text editor for instructions is appropriate for the audience

---

## 10. Implementation Roadmap

Given that the developer is working solo or in a small team, the following phased approach is recommended:

### Phase 1: Progress track only ✅ Done

Add the step sidebar/header as a static presentational element — no interaction, no collapse. Just a vertical list of numbered steps with completion state icons derived from template context variables already available in the view. This alone addresses Problem 1 (workflow opacity) with perhaps a day of work.

The view already computes `campaign.map_status`, `campaign.geo_limit`, `campaign.cities`, and `campaign.status`. Deriving step states from these is straightforward:

```python
def _get_step_states(campaign):
    steps = {}
    steps['basics'] = 'complete' if campaign.name and campaign.start_date else 'current'
    steps['hero'] = 'complete' if campaign.hero_image_effective_url else 'optional'
    steps['cities'] = _derive_city_step_state(campaign)
    steps['boundary'] = 'complete' if campaign.geo_limit else (
        'available' if campaign.map_status in ('ready', 'warning') else 'blocked'
    )
    steps['review'] = 'complete' if (campaign.geo_limit and campaign.map_status == 'ready') else 'blocked'
    steps['publish'] = 'complete' if campaign.status == 'published' else 'available'
    return steps
```

### Phase 2: Consolidate edit page into detail page ✅ Done

Move the basic metadata fields and city picker from `campaign_form.html` into inline sections on `campaign_detail.html`. This addresses Problem 3 (inconsistency) but requires creating new section-scoped save endpoints.

The create flow (`/manage/new/`) can remain as a separate shorter form since the user must pick a slug before a detail page exists. After creation, redirect to the new single-page detail view.

### Phase 3: Collapse/expand with `<details>` ❌ Attempted, abandoned

Add the `<details>` collapse behavior to each step section. Use the `open` attribute server-side to match step state. This is additive and can be done incrementally.

---

## 11. Out of Scope for This Proposal

- Promotion/sharing step (acknowledged in the problem statement as a future feature-add)
- Mobile-specific step track layout (responsive adjustments are needed but secondary to getting the structure right on desktop first)
- Campaign duplication / template campaigns (mentioned in PRD but not part of the manage detail flow)
