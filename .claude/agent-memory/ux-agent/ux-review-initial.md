# Initial UX Review — Worker Experience
Date: 2026-03-07
Scope: campaign_list.html, campaign_detail.html, map.js, about.html, views.py, models.py

## Files Reviewed
- /Users/sefk/src/stanford-vibecoding/leafletter-app/campaigns/templates/campaigns/campaign_list.html
- /Users/sefk/src/stanford-vibecoding/leafletter-app/campaigns/templates/campaigns/campaign_detail.html
- /Users/sefk/src/stanford-vibecoding/leafletter-app/campaigns/templates/campaigns/about.html
- /Users/sefk/src/stanford-vibecoding/leafletter-app/campaigns/static/campaigns/map.js
- /Users/sefk/src/stanford-vibecoding/leafletter-app/campaigns/views.py
- /Users/sefk/src/stanford-vibecoding/leafletter-app/campaigns/models.py

## Full Findings (see main review output for narrative)

### Critical
1. "Manage" link in campaign_detail header (line 177) — visible to all workers, links to /manage/<slug>/
   which redirects to login. Confusing and unnecessary for workers. Should be hidden or removed.

2. @csrf_exempt on log_trip view (views.py line 142) — not a UX issue per se but noted; workers
   submitting trips have no CSRF protection. Could enable spam/abuse that degrades data quality for
   all workers (fake coverage data).

### Major
3. No minimum zoom before selection mode — on city-level zoom, individual street segments are 1-2px
   wide and nearly impossible to tap. PRD section 6.3 notes "the map should zoom to at least street
   level before selection mode is active" but this is not implemented.

4. Trip form layout during selection mode: form (name/notes) appears above the toolbar but below the
   map. On mobile, worker taps "Log a Trip", is scrolled to the map, draws selections... then must
   scroll DOWN past the map to see the name/notes fields, then the "Add This Trip" button.
   The form and the submit button are separated by no visual grouping from the toolbar.

5. No pre-submit summary/confirmation — worker taps "Add This Trip" and trip is immediately POSTed.
   With no summary ("You selected 14 streets — submit?"), it's easy to accidentally submit a
   partially-built selection or wrong streets.

6. Loading state UX: the "Log a Trip" button shows "Loading streets... 0%" then "Loading coverage... 0%"
   on every page load. On a slow connection this can take many seconds. A worker who has used the app
   before may not understand why the button is disabled again. A separate, persistent loading indicator
   would be less disorienting than repurposing the primary CTA.

7. Lasso warning message (lasso-warning div) is technical and long:
   "Warning: the drawing did not pick up any streets. Consider drawing a larger loop. If your loop is
   outside the area with street coverage, it's likely not part of this campaign. Contact the Campaign
   Manager if you think that is in error."
   Non-tech-savvy workers on a phone standing on a sidewalk need something much shorter and actionable.

8. Campaign list: beta-banner is position:fixed (overlaps last campaign card on short screens). The
   campaign_detail page doesn't show the banner at all — inconsistent and the fixed positioning on
   the list page is a real usability issue.

### Minor
9. "Home" link in campaign_detail header is functional but styled identically to "Manage" — both are
   small text links in the top right. Workers may not notice or understand "Home" takes them to the
   campaign list. A back-arrow or breadcrumb would be clearer.

10. Selection counter says "N blocks" (map.js line 347). Workers think in terms of streets/roads,
    not OSM "blocks". Consider "N streets" or "N segments".

11. Instructions expand/collapse: the toggle button shows bare text "more" / "less" with no icon or
    visual container to signal it's interactive. Easy to miss.

12. Trip legend recorded_at shows only a date (YYYY-MM-DD format, views.py line 127). When multiple
    workers log trips on the same day, the legend entries look identical if they share a name
    (or are both anonymous). Showing time would differentiate entries.

13. Empty state: when no trips exist yet for a campaign, there is no legend, no coverage, and no
    message encouraging workers to be the first to log a trip. A motivating empty state ("No trips
    logged yet — be the first!") would set expectations and prompt action.

14. The campaign_detail page always shows a "Manage" link, even for fully anonymous workers with no
    manager account. This wastes header real estate and creates a confusing dead-end for curious workers
    who click it (they hit a login form).

15. about.html beta-banner is not position:fixed (it's a normal block at bottom of content) — differs
    from campaign_list behavior. Inconsistent.

16. No page title / meta description on campaign_detail that is useful for sharing — title is just
    "<campaign name> — Leafletter". No og: tags for social sharing of campaign links.

17. The "drawing-instructions" div (line 201) is hidden by default and only shown in selection mode,
    but the mobile-map-instructions div (line 197-199) is always shown on touch devices even outside
    selection mode. This means mobile users always see "Draw loops to select streets..." even when
    they haven't started a trip yet — slightly premature instruction.
