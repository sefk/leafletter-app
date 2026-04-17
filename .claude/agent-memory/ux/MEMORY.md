# UX Agent Memory — Leafletter App

## App Overview
- Campaign management tool for volunteer leafletting (door-to-door leaflet distribution)
- Django backend, PostGIS for street geometry, Leaflet.js for maps
- Two user types: Workers (public, no login) and Campaign Managers (authenticated)
- PRD lives at /Users/sefk/src/stanford-vibecoding/leafletter-app/PRD.md

## Key File Paths
- Public landing page: campaigns/templates/campaigns/campaign_list.html
- Worker map view: campaigns/templates/campaigns/campaign_detail.html
- About page: campaigns/templates/campaigns/about.html
- Map interaction JS: campaigns/static/campaigns/map.js
- Views: campaigns/views.py
- Models: campaigns/models.py
- URLs: campaigns/urls.py
- Manage templates: campaigns/templates/campaigns/manage/

## Design Conventions (confirmed)
- Brand color: #1a6b3c (dark green) — used for header bg, links, primary buttons, selected segments
- Typography: system-ui font stack (-apple-system, BlinkMacSystemFont, Segoe UI)
- Worker pages (campaign_list, campaign_detail, about) are standalone HTML — no {% extends %}
- Manage pages use campaigns/templates/campaigns/manage/base.html ({% extends %})
- Inline CSS throughout — no external stylesheet, no CSS framework
- Google Analytics: G-STEDJ3HETF on all pages
- Beta banner: position:fixed on campaign_list (web), safeAreaInset on iOS; static on about page
- Hamburger nav pattern used consistently across all pages
- iOS app: CampaignListView (native SwiftUI) + CampaignDetailView (WKWebView wrapping /c/<slug>/) + AboutWebView (WKWebView)

## iOS App Architecture
- APIClient.swift calls /api/campaigns/ — native list, not a web view
- CampaignDetailView injects JS: hides hamburger menu, disables pinch-to-zoom, scrolls toolbar into view on load
- CampaignDetailView disables iOS swipe-back gesture (edge pan) to prevent accidental nav during map interaction
- AboutWebView intercepts back-links and /about/ links to use native navigation instead
- Staging/production toggle hidden behind 3 taps on "About" title in nav bar
- Config.swift: Config.baseURL / Config.isStaging / Config.toggleStaging()

## Worker Flow (campaign_detail.html + map.js)
1. Page loads -> map renders -> streets + coverage load asynchronously
2. "Log a Trip" button is disabled with loading progress text during data fetch
3. Clicking "Log a Trip" enters selection mode: lasso draw or click/drag on individual segments
4. Trip form (name + notes, both optional) appears below map during selection mode
5. "Add This Trip" submits via fetch() POST to /c/<slug>/trip/
6. On success: map reloads coverage, status message shown, form clears
- Mobile instructions shown below map on touch/small screens
- Coverage toggle: "Coverage detail" / "Coverage summary" / "Hide coverage" select
- Trip legend with per-trip color swatches and visibility checkboxes (detail mode only)
- Undo button for deselecting last lasso batch or individual click

## Known UX Issues (updated 2026-04-17)

### Access Code Gate (issue #143, redesigned 2026-04-17)
- Original shipped design: yellow access-code-bar rendered below toolbar in DOM — off-screen on mobile; button disabled with no explanation; live debounced validation while typing; session persistence makes demoing hard.
- Two alternatives in draft PRs (not merged):
  - Approach A (branch ux/access-code-unlock-on-intent): button always enabled; gate deferred to click moment; unlock strip appears above map on click; validate on submit only; no persistence.
  - Approach B (branch ux/access-code-toolbar-inline): input moves inside toolbar (always in view); button stays disabled but has title tooltip; validate on submit only; no persistence.
- Hard constraint confirmed: gate must NOT block observers who just want to view coverage — only activates on intent to log.
- Session persistence (request.session flag) should be REMOVED from client — server-side 403 enforcement stays.
- Live keystroke validation is wrong pattern for passcode entry — validate on submit only.

### iOS Worker
- CampaignRow: TestBadgeView component defined separately but plain-text TEST badge duplicated inline in else branch (code inconsistency, not UX issue per se)
- "Map generating…" label appears on campaign list row — good indicator, but no explanation of what to do (check back later)
- BannerView entire text block uses onTapGesture — whole banner is tappable but only "About this app" is styled as a link; confusing tap target
- Campaign list loads correctly show hero images but hero image height is fixed at 150px — very short, cuts faces/details
- No loading shimmer/placeholder — campaigns show ProgressView spinner only, no skeleton rows
- Error retry uses TaskGroup pattern correctly; Retry button placement (bottom overlay on ContentUnavailableView) is good

### Web Worker — campaign_detail.html
- "Manage campaign" link in hamburger menu visible to all visitors — exposes /manage/<slug>/ URL; unauthenticated access redirects to login but label is confusing for workers
- Trip form (name + notes) rendered above toolbar in DOM, but because toolbar is above it visually (DOM order: form then toolbar), on mobile the form appears below the fold during selection mode — worker must scroll down
- Lasso warning: zero streets message is well-placed now (between drawing-instructions and trip-form)
- "Add This Trip" button appears with no selection count visible confirmation before submit
- coverage-mode select default is "Coverage summary" on page load (map.js sets coverageMode = 'summary') but HTML select default value is also "Coverage summary" — consistent
- No empty-state when no trips yet logged: map just shows grey streets with no orange overlay and legend is hidden — which is fine but could be more welcoming for first visitor
- campaign_list.html uses location.replace() so back button from detail skips the list — intentional (matches iOS behavior, prevents web back-nav confusion)

### Manager UI — manage/campaign_detail.html
- Slug field still says "It cannot be changed once created" — incorrect per product decision (editable before publish). Should be fixed.
- Step sidebar: all steps always visible and numbered, but steps 4 (Boundary) and 5 (Review) show as "blocked" with grey circles when cities aren't fetched yet — no tooltip explaining why they're blocked
- Boundary section only renders if campaign.bbox exists — if no cities yet, entire boundary section is absent with no placeholder message explaining next step
- Re-download Streets button has no confirmation; destructive (clears existing blocks) with no warning
- Delete confirmation uses browser confirm() — adequate but no undo path shown
- Admin owner-change form embeds all basics fields as hidden inputs to avoid blanking them on save — fragile pattern, easy to accidentally drop a field

## Data Model Notes
- Campaign.instructions stores HTML (rich text from manager)
- Trip.worker_name and notes are optional (good)
- Streets are pre-fetched OSM segments stored as PostGIS LineStrings
- Coverage computed server-side per request (no caching in coverage endpoint)
- Trip deletion is soft-delete (deleted=True flag)

## Manage UI — Product Decisions (2026-04-07)
- Slug policy: editable before publish, locked after publish. "Cannot change once created" is wrong — remove that copy everywhere.
- New campaign flow: product owner wants /manage/new/ to be a single-field form (name only, slug auto-derived). Full setup happens in the step-based detail page.
- Cities step is interactive / ongoing, not a one-time import — add/remove/re-fetch at any time, even post-publish.
- Full proposal at doc/ux-manage-proposal.md (updated 2026-04-07 with Phase 4).

## Island-in-the-City Problem (Issue #136, reviewed 2026-04-02)
- OSM unincorporated holes are common in US suburbs (CDPs, unincorporated neighborhoods inside city boundaries)
- Current workaround (download county + draw polygon) is unusable for non-technical managers
- Recommended approach: Option 4 as named-place search — let managers add CDPs/unincorporated areas by name (Nominatim/OSM admin boundary search), same UI pattern as city picker
- Key UX principle: worker experience should be identical — just streets, no visible indication of how the boundary was sourced
- Option 2 (auto-fill) is the ideal long-term goal but detection of "holes vs. normal gaps" is risky to get wrong
- Options 1 (freeform) and 3 (placeholder blob) both break the street-level mental model workers rely on
- map.js lasso handler only captures layers in layerToId — blank map areas produce nothing; Options 1/3 would require parallel data model
