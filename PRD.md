# Leafletter — Product Requirements Document

**Version:** 0.2 (MVP)
**Date:** 2026-02-22
**Status:** Draft

---

## 1. Overview

Leafletter is a web-based coordination tool for volunteer leafletting campaigns. It helps small teams of volunteers distribute printed leaflets door-to-door across neighborhoods and towns by providing a shared map that tracks which streets have already been covered.

Leafletting is an inexpensive and effective way to publicize an upcoming event (such as a political protest or election) or to raise awareness for a cause. Leafletter makes it easy for distributed, uncoordinated volunteers to work efficiently without duplicating effort or missing streets.

### 1.1 Goals

- Allow a Campaign Manager to define a leafletting campaign in minutes
- Give Workers a dead-simple, no-login interface to see what streets have been covered and to log their own trips
- Ensure accumulated trip data is durable and recoverable

### 1.2 Non-Goals (MVP)

- Real-time map updates
- Leaflet hosting or inventory management
- Worker authentication or accounts
- Resupply / logistics workflows
- Integration with demographic or voting datasets

---

## 2. Users and Roles

### 2.1 Campaign Manager

Responsible for creating and managing campaigns. There will be a small number of Campaign Managers in the system overall. Managers log in with a username and password (see Section 8.5).

**Capabilities:**
- Create, update, and delete campaigns
- Preview a campaign as a Worker before publishing
- View and moderate leafletting trip records

### 2.2 Worker

A volunteer who walks door-to-door delivering leaflets. Workers may not be tech-savvy; ease of use is the top priority for this role.

**Capabilities:**
- Access a campaign via a simple URL — no login required
- View campaign information and a coverage map
- Log a leafletting trip (streets covered + optional name and notes)

---

## 3. Campaign Data Model

A **Campaign** is the primary unit of organization.

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | e.g. "No Kings 3" |
| `slug` | string | yes | URL-safe identifier; auto-generated from name; editable before launch; locked after launch; e.g. "nokings3" |
| `goal` | text | yes | Short description of the campaign's purpose |
| `cities` | list of strings | yes | Cities/towns to be leafletted; used to generate the initial map extent |
| `start_date` | date | yes | Defaults to today |
| `end_date` | date | no | Optional campaign end date |
| `instructions` | text | no | General instructions for workers (e.g. safety tips, local rules about mailboxes) |
| `materials_url` | url | no | Link to leaflet PDF or other materials |
| `contact_info` | text | no | Email and/or phone number for workers to reach the organizer |
| `status` | enum | yes | `draft`, `published`, `deleted` |
| `map_status` | enum | yes | `generating`, `ready`, `error` — tracks OSM geometry fetch state |
| `created_at` | timestamp | yes | |
| `updated_at` | timestamp | yes | |

**Notes:**
- Campaigns with `status = deleted` are hidden from all views but retained in the database for manual recovery.
- The slug becomes the public URL segment: `leafletter.app/c/<slug>`.

---

## 4. Trip Data Model

A **Trip** represents one worker's leafletting session.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | uuid | yes | Primary key |
| `campaign_id` | foreign key | yes | |
| `streets` | geometry | yes | Encoded set of street segments covered; see Section 7 |
| `worker_name` | string | no | Self-reported; e.g. "Jane Smith" |
| `notes` | text | no | e.g. "Only did the south side of the street" |
| `recorded_at` | timestamp | yes | Server time when the trip was submitted |

Trips are stored as individual records so that any single trip can be deleted without affecting other data (e.g. in the event of a misuse or error).

---

## 5. Manager Workflows

### 5.1 Create a Campaign

1. Manager navigates to the admin dashboard and clicks **New Campaign**.
2. Manager fills in the campaign form (see Section 3 for fields).
3. The slug is auto-generated from the name but is editable at this stage.
4. Manager clicks **Save as Draft**. The campaign record is saved immediately.
5. A background job fetches OSM street geometry for the specified cities and builds the map. The campaign detail page shows a **"Generating map…"** status indicator until the job completes, at which point it shows **"Map ready"**. The manager cannot publish until the map is ready.

### 5.2 Preview a Campaign

- From the campaign detail page, the manager can click **Preview** to see the campaign exactly as a Worker would, including the map and trip coverage overlay.

### 5.3 Publish a Campaign

- The manager clicks **Publish**. The campaign's status changes from `draft` to `published`.
- The public URL (`/c/<slug>`) becomes accessible.
- After publishing, the following fields are **locked** and cannot be edited:
  - `slug`
  - `cities` list

### 5.4 Update a Campaign

- All unlocked fields can be edited at any time after publication.
- The manager can view a list of all **Trips** associated with the campaign, showing:
  - Worker name (if provided)
  - Notes (if provided)
  - Timestamp
- The manager can **delete** any individual trip (e.g. for error correction or misuse), with a confirmation step.

### 5.5 Delete a Campaign

- The manager clicks **Delete Campaign** on the campaign detail page.
- A confirmation dialog is shown, stating clearly: **"This cannot be undone."**
- On confirmation, the campaign's `status` is set to `deleted`. The campaign is hidden from all user-facing views.
- The underlying database record and associated trip data are retained for potential manual recovery.

---

## 6. Worker Workflows

### 6.1 Accessing a Campaign

- Workers navigate to `leafletter.app/c/<slug>` — typically via a link shared by the Campaign Manager.
- No login or account is required.
- The page displays:
  - Campaign name and goal
  - Start/end dates
  - Instructions and safety notes
  - Materials link
  - Contact information
  - The street-level coverage map (see Section 7)

### 6.2 Browsing the Map

- The map shows the full area covered by the campaign cities.
- Workers can pan and zoom freely.
- Streets that have already been leafletted are highlighted (see Section 7).
- The highlight layer can be toggled on/off to make navigation easier.
- The map must be usable on a mobile phone browser.

### 6.3 Logging a Trip

1. Worker taps/clicks **Log a Trip** (or equivalent prominent CTA on the map view). The map enters selection mode.
2. Worker taps individual street segments on the map to select them. Selected segments are highlighted in a distinct color. Tapping a selected segment deselects it.
3. Worker taps **Done selecting** when finished.
4. Worker optionally enters:
   - Their name
   - Any notes
5. Worker taps **Submit**. The trip is saved and the coverage map updates to include the new streets on next page load.

**UX note on tap targets:** Street segments are narrow and can be difficult to tap precisely on mobile. The implementation should use a generous tap radius (hit area larger than the visual line width) to make selection forgiving. The map should zoom to at least street level before selection mode is active.

**Note:** Dynamic map updates are not required for MVP. The updated coverage will be visible on next page load.

---

## 7. Map and Coverage Visualization

### 7.1 Map Provider

The map should display:
- Street-level detail
- Landmarks (schools, churches, parks)
- Neighborhood names where available

Recommended open data source: **OpenStreetMap** via a library such as Leaflet.js or MapLibre GL. Map tiles are served by a **third-party hosted tile service** (Stadia Maps or Protomaps CDN). Free tiers are sufficient for MVP scale; no self-hosted tile infrastructure is required.

### 7.2 Coverage Overlay

- Streets that have been leafletted are highlighted directly on the map, similar in style to Google Maps' Street View coverage lines.
- The highlight represents the **union** of all trips logged for the campaign — individual trip boundaries need not be visible.
- The overlay must be toggleable (on/off) for easier navigation.

### 7.3 Street Geometry

Street geometry data comes from OpenStreetMap. When a campaign is saved, a **background job** (Celery) fetches and stores OSM road segments for the specified cities. The campaign shows a "Generating map…" status until this completes.

Storage and query approach:
- OSM street segments are stored in the database as PostGIS geometries, keyed by OSM way ID.
- Trip records store a set of OSM segment IDs (not raw geometries).
- Coverage is computed as the union of all segments across all non-deleted trips for a campaign, and returned as GeoJSON for the frontend to render.
- Segments are scoped per campaign, so a segment in two different campaigns is stored twice (keeping campaigns independent and deletable).

**Source:** OSM data can be fetched via the Overpass API at campaign creation time, using a bounding box or named area query for the specified cities.

---

## 8. Technical Requirements

### 8.1 Platform

- Web application, accessible via desktop and mobile browsers
- Python backend (framework TBD; FastAPI or Django are natural fits)
- PostgreSQL with PostGIS extension for street geometry storage
- No native mobile app required

### 8.2 URLs

| Path | Description |
|---|---|
| `/` | App home / landing page |
| `/admin/` | Campaign Manager dashboard (auth-protected) |
| `/admin/campaigns/new` | Create campaign form |
| `/admin/campaigns/<id>` | Edit / manage a campaign |
| `/c/<slug>` | Public campaign page (Worker view) |

### 8.3 Deployment

- Single fixed domain: `leafletter.app`. All campaigns live under `/c/<slug>`.
- Standard web hosting; exact hosting provider is out of scope for this document.
- The app should be stateless and horizontally scalable (sessions managed via cookies/tokens).

### 8.5 Authentication

Campaign Managers authenticate with a **username and password**. No SSO or magic links for MVP.

- Session-based auth (e.g. Django sessions) or short-lived JWT tokens are both acceptable.
- No self-service registration — accounts are created by an administrator.
- Worker-facing pages (`/c/<slug>`) require no authentication.

### 8.4 Performance and Scale

MVP is expected to support:
- O(10) active campaigns at a time
- O(100) workers per campaign
- O(1000) trips per campaign

No special caching or real-time infrastructure is required at MVP scale.

### 8.6 Background Jobs

OSM geometry fetching at campaign creation runs as a background job.

- **Queue:** Celery with MySQL as the broker
- **Tasks:** `fetch_osm_segments(campaign_id)` — queries Overpass API, stores segments in PostGIS, updates campaign `map_status` field (`generating` → `ready` or `error`)
- The admin UI polls or refreshes to reflect the current `map_status`.

---

## 9. UX Principles

1. **Workers first.** The worker interface must be operable by someone who is not tech-savvy, on a phone, possibly while standing on a sidewalk. Minimize required steps and input.
2. **No login for workers.** The URL is the access mechanism. Friction-free entry.
3. **Progressive disclosure.** Show workers what they need; advanced campaign details should not clutter the map view.
4. **Forgiving data entry.** Worker name and notes are optional. A trip with only street selection should be completable in under a minute.

---

## 10. Out of Scope (Post-MVP)

The following features are explicitly deferred:

| Feature | Notes |
|---|---|
| Leaflet hosting | MVP uses an external URL; future could host PDFs directly |
| Multiple materials per campaign | MVP supports one URL |
| Inventory and resupply management | Future: depot tracking, resupply requests |
| Real-time map updates | Future: live coverage updates without page refresh |
| Demographic / voting data overlays | Future: correlate coverage with household or electoral data |
| Worker accounts and history | Future: track individual worker contributions over time |

---

## 11. Resolved Decisions

The following questions were discussed and resolved during initial planning.

| # | Question | Decision | Rationale |
|---|---|---|---|
| 1 | Street selection UX on mobile | **Tap individual street segments** | Most precise; straightforward to implement. Hit area should be larger than visual line width to ease tapping. The risk of tedium for long routes is accepted at MVP scale. |
| 2 | Map tile hosting | **Third-party hosted service** (Stadia Maps or Protomaps CDN) | No infrastructure to manage; free tiers cover MVP scale. |
| 3 | Manager authentication | **Username + password** | Simple, no external dependencies. Accounts created by an administrator; no self-service registration. |
| 4 | Campaign URL base | **Single fixed domain** (`leafletter.app/c/<slug>`) | Trivial to deploy and easy for managers to share links. |
| 5 | OSM geometry fetch at campaign creation | **Background job** (Celery) | Avoids blocking the form submission for a potentially long operation; provides a clear "Generating map…" → "Ready" status in the UI. |

## Colophon

This was written by Claude Code, starting with detailed input [here](https://docs.google.com/document/d/1Z57Y39GXIw4cuJiibijPU0hWBx0ayz88G-mxyV3Yb_4/edit?usp=sharing).
