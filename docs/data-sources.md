# Data sources

What real data this project uses, what each source actually publishes, and how it is
fetched. Everything below was checked on 2026-10-27 against the live portals. Nothing
beyond the small inspection requests listed at the end has been downloaded yet.

Labels used across the repo: **311** (real city data), **simulation** (generated campus
and SimPy runs), **pilot** (real reports from a pilot, if one happens).

## Fetching rules

These apply to every request a loader in `data311/` or the weather client makes.

- Read the host's `robots.txt` and terms before fetching. Copies of the robots files read
  on 2026-10-27 are summarised below.
- Use only the official download links or documented API endpoints.
- Send `User-Agent: CampusTrace/0.1 (portfolio research project; contact: $CONTACT_EMAIL)`.
- Wait at least the host's `Crawl-delay` between requests (1 s for SF, 10 s for San Jose).
- Fetch each file once. Cache it under `data/raw/<source>/` (gitignored) with a sidecar
  `.meta.json` recording the URL, fetch time, HTTP status, byte count and SHA-256. A
  loader that finds a cached file uses it and does not refetch.
- If a request is refused (403, 429, robots disallow), stop and report it. No retries
  through other paths.

## San Francisco 311 Cases

| | |
|---|---|
| Dataset | 311 Cases, ID `vw6y-z8j6` |
| Portal | `data.sf.gov` (Socrata). `data.sfgov.org` now 301-redirects to `data.sf.gov`. |
| License | Open Data Commons Public Domain Dedication and License (PDDL) 1.0 |
| Updates | Data changes several times an hour; published daily |
| Volume | 872,427 cases opened in 2025, across 42 categories |

**robots.txt** (`https://data.sf.gov/robots.txt`): `Crawl-delay: 1`. Disallows browse
and catalog search pages, `/OData.svc/`, `/api/odata/`, `/api/collocate*`, `/tiles/`,
login pages. The SODA endpoint `/resource/vw6y-z8j6.json|csv` and the metadata endpoint
`/api/views/vw6y-z8j6.json` are not disallowed.

**Endpoint.** `GET https://data.sf.gov/resource/vw6y-z8j6.csv` with SoQL parameters
`$select`, `$where`, `$order`, `$limit`, `$offset`. The plan suggests a free app token
(`X-App-Token` header) to raise the rate limit. Getting one means creating a Socrata
account, which I have not done; without a token, requests are anonymous and throttled.

### Columns (from `/api/views/vw6y-z8j6.json`)

| Field | Type | Portal description (shortened) |
|---|---|---|
| `service_request_id` | number | Case ID |
| `requested_datetime` | timestamp | When the request was made |
| `closed_date` | timestamp | When the request was closed |
| `updated_datetime` | timestamp | Last modified |
| `status_description` | text | Open / Closed |
| `status_notes` | text | Why the status changed, written by the responding agency |
| `agency_responsible` | text | Responding agency |
| `service_name` | text | Category (e.g. "Street and Sidewalk Cleaning") |
| `service_subtype` | text | Request type (e.g. `garbage_and_debris`) |
| `service_details` | text | Request details (e.g. `other_loose_garbage_debris_yard_waste`) |
| `address`, `street` | text | Location |
| `supervisor_district`, `neighborhoods_sffind_boundaries`, `analysis_neighborhood`, `police_district`, `bos_2012` | | Area codes |
| `lat`, `long`, `point`, `point_geom` | | WGS84 location |
| `source` | text | Phone, Web, Mobile App, etc. |
| `media_url` | url | Photo link, when one was attached |
| `data_as_of`, `data_loaded_at` | timestamp | Load bookkeeping |
| `:@computed_region_*` | number | Portal-computed region IDs (ignored) |

### Fields we will pull

`service_request_id, requested_datetime, closed_date, status_description, status_notes,
agency_responsible, service_name, service_subtype, service_details, address, lat, long,
source`. Not `media_url`: following those links means fetching third-party images of
people's surroundings, which the evaluation does not need.

### What is actually there, compared with the plan

- **No reporter free text.** The public dataset has no description field. The only
  free text is `status_notes`, written by city staff when they change a case's status
  (e.g. "case_resolved - Officer responded … Unable to Locate."). `service_details` is a
  fixed label, not the reporter's words. See "Decisions (2026-10-27)" below.
- **Duplicate labels exist.** `status_notes` marks duplicates in several forms ("Case is
  a Duplicate", "case_is_a_duplicate - …", "Memo:Duplicate of 1001244235",
  "*DUPLICATE COMPLAINT*"). 69,427 cases opened in 2025 contain "DUPLICATE" there. Some
  name the original case number, which gives true pairs, not only a flag.
- **Urgency signal.** `requested_datetime` and `closed_date` give time to close. Two
  categories carry an explicit priority in their name ("MTA Parking Traffic Signs High
  Priority" / "Normal Priority").
- **Trades.** Most volume is street-level (cleaning, parking, graffiti, encampments).
  Categories with a plausible facilities trade include Streetlights, Sewer, Water
  Quality, Waste of Water, Residential Building, Damage Property, Sidewalk and Curb,
  Street Defect, Tree Maintenance, Litter Receptacle Maintenance and Street and Sidewalk
  Cleaning. The category-to-trade mapping is written and reviewed in the Nov 28 work.

### Pull plan

Per calendar year, one SoQL query paged by `$offset` in blocks of 50,000 rows, ordered by
`service_request_id`, at most one request per second, written to
`data/raw/sf311/<year>-<category-slug>.csv`. Scope (decided 2026-10-27): 2024 and 2025,
limited to the categories Kavya approves from the aggregate-by-category proposal. The
full pull does not start before that approval.

## San Jose 311 Service Request Data

| | |
|---|---|
| Dataset page | `https://data.sanjoseca.gov/dataset/311-service-request-data` |
| Portal | CKAN (OpenGov) |
| License | Creative Commons CCZero |
| Files | One CSV per year, 2017 to 2025 (static), 2026 (updated daily) |
| Size | 2024: 41.8 MB, 2025: 44.9 MB, 2026 so far: 37.0 MB (about 350,000 rows a year) |

**robots.txt** (`https://data.sanjoseca.gov/robots.txt`): `Crawl-delay: 10`. Disallows
`/api/` (so the CKAN API is off limits), `/datastore/*`, `/user/*`, `/dataset/activity/*`,
`/dataset/*/issues/*` and others. The dataset page and its `/resource/<id>/download/<file>.csv`
links are not disallowed. Those links 302-redirect to a presigned S3 URL; the S3 URL
accepts GET (it returns 403 to HEAD because the signature covers GET only).

**Download links** (from the dataset page):

| Year | Resource ID |
|---|---|
| 2017 | `6399d341-880c-49eb-8cb6-5fb294bf48dc` |
| 2018 | `d4746f5f-fe13-440b-8488-0d72ca8662c7` |
| 2019 | `c5c5034a-b238-440c-84b5-5654799ca159` |
| 2020 | `850c5060-ccf6-4515-82d6-c582dcd8e546` |
| 2021 | `4b0563f3-ed85-4889-8c39-05f160dbe0eb` |
| 2022 | `0f26e09e-9e1f-4305-9d2d-fb5d8292ae8a` |
| 2023 | `00b1c1ba-b83e-40a0-8260-e634c57e4fcc` |
| 2024 | `ac55d3cd-b4fe-4f96-bf12-4ce3191c8bd4` |
| 2025 | `761d15cc-0e89-46f4-a35b-0e586d746edf` |
| 2026 | `d886727c-60f1-4be7-9a30-f6806375b1a3` |

URL pattern: `https://data.sanjoseca.gov/dataset/5bd6605f-43fc-4ccc-b80b-3c15b5a02319/resource/<resource-id>/download/311-service-requests-<year>.csv`

### Columns (header of the 2025 file)

`Incident_ID, Status, Source, Category, Service Type, Latitude, Longitude, Date Created,
Date Last Updated, Department`

From the first 522 rows of 2025:

- `Status`: Closed, Open, In Progress, ReAssigned
- `Source`: VBCS Web, SFDC-DOT, Email
- `Category`: blank for 87% of rows; otherwise a sub-label such as Other, Utility Box, Painted Wall
- `Service Type`: Junk pickup, Vehicle Concerns, Graffiti, Container Issues, Illegal
  Fireworks, Illegal Dumping, Other Issues, Streetlight Outage, Encampment Concerns, …
- `Department`: blank for 48% of rows; otherwise DOT, Parks and Recreation, Code Enforcement, ESD, …
- `Latitude`/`Longitude`: some rows are `0,0` (no location)
- Dates are `M/D/YYYY h:mm:ss AM` local time

### What is actually there, compared with the plan

- **No free text at all.** No description, no staff notes.
- **No closed date.** Only `Date Last Updated`; for Closed rows it is the best available
  proxy for close time, but any later edit moves it.
- **No duplicate marker.** Duplicate labels for San Jose have to come from the hand-labelled
  candidate pairs the plan describes (same service type, nearby, within 72 hours), judged
  on category, location and time only.

### Pull plan

Download the 2024 and 2025 static files once each, 10 seconds apart, to
`data/raw/sj311/`. The daily 2026 file is fetched at most once, and the snapshot date is
recorded in its `.meta.json`.

## SJ311 language translation pairs (checked, not used)

`https://data.sanjoseca.gov/dataset/sj311-language-translation-automl-training-pairs`
(CC0) looked like a possible source of real request wording. The first 8 KB of the
English-Spanish file shows it holds website and app strings ("Pothole", "What would you
like to report?", COVID-19 notices), not resident reports. Not used.

## National Weather Service API (not used)

`https://api.weather.gov/robots.txt` returns:

```
User-agent: *
Disallow: /
```

The API's documentation asks for a descriptive User-Agent and the API is meant for
programs, but its robots file disallows every path for every agent. Decision
(2026-10-27): the API is not called. Weather is simulated from a seeded config so every
policy and seed sees the same conditions, and the plan's `weather_now` tool reads the
simulated series.

## Decisions (2026-10-27)

1. **Report text.** Neither city publishes the reporter's own words. Real 311 data is
   used for structure only. Report text for intake, trade assignment and text-based dedup
   comes from the persona simulator and is labelled simulation. Personas and wording
   rules are written and frozen before any agent is tuned.
2. **Duplicates (311).** San Francisco's `status_notes` DUPLICATE markers are the labels.
   The test sees only category, location and time window, on SF 2024-2025, and pairs a
   duplicate with its original where the note gives the original case number. San Jose
   has no duplicate field and is not part of this test.
3. **Urgency.** SF has time to close; San Jose only has last-updated time, so the urgency
   signal comes from SF.
4. **Weather.** Simulated (see above).
5. **Socrata app token.** Kavya will create the account and add `SOCRATA_APP_TOKEN` to
   `.env`. Until then SF requests are anonymous, small, and at least 1 second apart.
6. **SF pull scope.** 2024-2025. An aggregate query by category comes first; the full
   pull waits for Kavya to approve the category-to-trade list.
7. **Contact email.** Every request reads `CONTACT_EMAIL` and refuses to run if it is
   unset. No fallback address.
8. **Other cities.** Up to three other 311 feeds get a one-hour check for published
   descriptions (robots and terms first, samples only). Result recorded below.

## Other cities' 311 feeds

Not checked yet: waiting on `CONTACT_EMAIL`.

## Inspection requests made on 2026-10-27

All with the User-Agent above, with the host's crawl delay between requests.
`CONTACT_EMAIL` was not set at the time, so these requests carried Kavya's address
directly. From now on the client reads only `CONTACT_EMAIL` and refuses to run without it.

| Host | Request | Why |
|---|---|---|
| data.sfgov.org | `GET /robots.txt` (301 to data.sf.gov) | robots |
| data.sf.gov | `GET /robots.txt` | robots |
| data.sf.gov | `GET /api/views/vw6y-z8j6.json` | column list, license |
| data.sf.gov | `GET /resource/vw6y-z8j6.json` 40 rows from 2026-09-01 | see field contents |
| data.sf.gov | 2025 counts by `service_name` (aggregate, 42 rows) | category volume |
| data.sf.gov | 2025 count and 15 examples of `status_notes` containing DUPLICATE | duplicate labels |
| data.sanjoseca.gov | `GET /robots.txt` | robots |
| data.sanjoseca.gov | `/dataset/?q=311`, the dataset page, the 2025 resource page | find files, license |
| data.sanjoseca.gov | 2025 CSV: HEAD (403 from S3), then bytes 0-65535 | column header and sample |
| data.sanjoseca.gov | 2024, 2025, 2026 CSV: bytes 0-0 | file sizes |
| data.sanjoseca.gov | translation pairs dataset page, en-es TSV bytes 0-8191 | check for request text |
| api.weather.gov | `GET /robots.txt` | robots |
