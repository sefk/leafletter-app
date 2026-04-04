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
- No base template/inheritance — each page is standalone HTML (no {% extends %})
- Inline CSS only — no external stylesheet, no CSS framework (no Bootstrap/Tailwind)
- Google Analytics: G-STEDJ3HETF (campaign-specific token, different from global tokens in CLAUDE.md)
- Beta banner: fixed bottom on campaign_list, static bottom on about page (inconsistency)

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

## Known UX Issues (from initial review, 2026-03-07)
See: ux-review-initial.md for full details

Critical:
- "Manage" link visible to all workers in header — exposes internal URL, confusing
- log_trip endpoint uses @csrf_exempt — security concern noted

Major:
- No zoom enforcement before selection mode — small segments are very hard to tap on mobile at city zoom level
- Trip form appears ABOVE toolbar during selection, meaning worker must scroll down past map to find it
- "Add This Trip" button label doesn't match PRD's "Done selecting" / "Submit" two-step flow description
- No confirmation or summary before final submit — easy to accidentally submit with wrong segments
- Loading state repurposes the primary CTA button text — disorienting on repeat visits
- Lasso warning text is very long and technical for non-tech-savvy workers
- beta-banner is position:fixed on campaign_list (obscures bottom content) but not on campaign_detail

Minor:
- No back-navigation from campaign_detail to campaign_list (header "Home" link present but not prominent)
- "blocks" terminology in selection counter may confuse workers (they think in streets/roads)
- Instructions collapse/expand uses bare "more"/"less" with no visual affordance
- recorded_at in legend shows ISO date only (no time), which matters when multiple trips same day
- No empty-state messaging when no coverage exists yet

## Data Model Notes
- Campaign.instructions stores HTML (rich text from manager)
- Trip.worker_name and notes are optional (good)
- Streets are pre-fetched OSM segments stored as PostGIS LineStrings
- Coverage computed server-side per request (no caching in coverage endpoint)
- Trip deletion is soft-delete (deleted=True flag)

## Island-in-the-City Problem (Issue #136, reviewed 2026-04-02)
- OSM unincorporated holes are common in US suburbs (CDPs, unincorporated neighborhoods inside city boundaries)
- Current workaround (download county + draw polygon) is unusable for non-technical managers
- Recommended approach: Option 4 as named-place search — let managers add CDPs/unincorporated areas by name (Nominatim/OSM admin boundary search), same UI pattern as city picker
- Key UX principle: worker experience should be identical — just streets, no visible indication of how the boundary was sourced
- Option 2 (auto-fill) is the ideal long-term goal but detection of "holes vs. normal gaps" is risky to get wrong
- Options 1 (freeform) and 3 (placeholder blob) both break the street-level mental model workers rely on
- map.js lasso handler only captures layers in layerToId — blank map areas produce nothing; Options 1/3 would require parallel data model
