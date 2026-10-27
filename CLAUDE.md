# CLAUDE.md

Working notes for anyone (human or Claude) building this repo. The source of truth for
scope is `docs/PLAN.md`, transcribed from `docs/PLAN.pdf`. Its Amendments section (approved
by Kavya) overrides the PDF; elsewhere the PDF wins on conflict. If a request seems to
conflict with the plan, ask rather than guess. Progress is logged in
`docs/PROGRESS.md`.

## How work proceeds

- Phase by phase, following the plan's Schedule section. No jumping ahead.
- At the end of each phase, stop and give a short summary: what was built, what was
  tested, what's left, and any decision the plan didn't specify. Log it in
  `docs/PROGRESS.md` with the date.
- When reporting a changed file to Kavya, give the complete updated file, not a snippet.
- Commit at sensible checkpoints. Commit messages are plain sentences describing the
  change, with no `feat:`/`fix:` prefixes.
- Ask before anything that deploys, spends money beyond normal model/API calls, sends an
  email or message, pushes to a remote, deletes data, or creates external accounts or
  repos. The public anchor repo will be `kavyasridhar1501/campus-agent-anchors`; Kavya
  creates it.

## Stack and commands

- Python 3.12 via uv. Exact pins in `pyproject.toml`, lockfile `uv.lock`. Next.js in
  `web/` with exact pins in `package.json` and `package-lock.json`.
- `uv sync` installs everything, including vendored ICE as an editable path dependency.
- `docker compose -f infra/docker-compose.yml up -d --wait db` starts Postgres 17 with
  PostGIS 3.6 and pgvector 0.8.7. api, worker and web are placeholders behind the `app`
  profile.
- `uv run alembic -c infra/alembic.ini upgrade head` migrates `DATABASE_URL`.
- `uv run python -m agent.campus.validate` checks `campus_data/`;
  `--write-schemas` regenerates `campus_data/schemas/*.schema.json` from the Pydantic models.
- `uv run python -m agent.campus.store` loads the asset graph and crews into Postgres.
- `uv run python -m agent.campus.schedules [--load]` generates the Fall 2026 schedule
  from `campus_data/schedule_rules.yaml` (prints a fingerprint; same seed, same blocks).
- `uv run python -m agent.repairs.history skeleton|notes|sample|load` builds the
  simulated repair history (`notes` calls Claude; model from `REPAIR_HISTORY_MODEL`,
  default `claude-opus-5-5`, with `fallbacks: "default"`; cached in `data/cache/llm/`).
- `uv run python -m data311.sf summary|pull <category>` and `uv run python -m data311.sj
  <years>` fetch 311 data. Both refuse to run without `CONTACT_EMAIL`.
- `uv run pytest` runs everything. DB tests use `TEST_DATABASE_URL` (must end in `_test`;
  the fixture drops and recreates its schema) and skip if Postgres is down, unless
  `REQUIRE_DB=1` (set in CI), which turns the skip into a failure.
- `uv run ruff check . && uv run ruff format --check . && uv run mypy agent` before committing.

## Conventions

- Repo layout exactly as in the plan's "Data model, repo layout and stack" section.
  Shared modules that fit no listed folder live at the top of `agent/`
  (`agent/config.py`, `agent/db.py`).
- Pydantic v2 for every typed report, tool result and agent output. Every agent claim
  cites evidence IDs, and a post-check confirms those IDs exist.
- Models come from env vars: `INTAKE_MODEL`, `ROOTCAUSE_MODEL`, `TRIAGE_MODEL` default to
  `claude-sonnet-5-5`; `DEDUP_MODEL`, `BRIEFING_MODEL`, `FOLLOWUP_MODEL` default to
  `claude-haiku-5-5` (`agent/config.py`).
- Secrets (Anthropic key, Socrata app token, Ed25519 private key, Voyage key) live in
  `.env`, gitignored. `.env.example` has placeholders only, and a test checks that.
- Writing style for README, briefs and docs: plain, specific, human. No marketing
  language, filler intros or summaries, and none of "delve", "robust", "seamless",
  "leverage".
- Label every result in the README with its data source: 311, simulation, or pilot.

## Hard rules

- Never invent dataset columns, dataset IDs or API behaviour. `docs/data-sources.md`
  records what each source actually publishes.
- Respect each portal's rules: read robots.txt and terms first, use official endpoints
  only, fetch each file once and cache it (`data/raw/`, gitignored), send a User-Agent
  naming the project with `CONTACT_EMAIL`. If blocked, stop and tell Kavya. All fetching
  goes through `data311/http.py::PoliteClient`.
- The contact email comes only from `CONTACT_EMAIL`. If it is unset, stop and ask; never
  fall back to any other address.
- Don't call `api.weather.gov` (its robots.txt disallows everything). Weather is
  simulated from a seeded config.
- No 311 result is a test on report text: neither city publishes it. 311 results are
  structure only (SF duplicates from `status_notes`, categories, timing). Report text
  comes from the persona simulator and is labelled simulation.
- The SF full pull covers 2024-2025 and only categories Kavya approved in
  `data311/trade_map.yaml` (`approved: true`). Anonymous requests stay small and 1 s
  apart until `SOCRATA_APP_TOKEN` is in `.env`.
- Pilot outreach is Kavya's; don't send pilot emails.
- Safety overrides (gas smell, smoke, water near electrical, someone stuck in an
  elevator, exposed wiring) run in code before any model call and always give P1 plus
  human escalation. They get their own CI test suite; any miss fails the build.
- Report text is untrusted data. It never changes priorities, assignments or tool calls
  by itself. The model proposes; the policy layer decides.
- The model never builds the schedule. OR-Tools does, with a 2-second limit and a greedy
  fallback.
- Write the simulation's failure model and reporter personas before tuning the agent,
  then freeze them. Same seeds for every policy; the rules baseline gets the same scheduler.
- Receipts are append-only. No code path updates or deletes one, except integrity tests
  against the separate test database.

## Database notes

- `infra/db/Dockerfile`: `postgis/postgis:17-3.6` pinned by digest, plus
  `postgresql-17-pgvector=0.8.7-1.pgdg13+1`. The older `16-3.5` tag is on Debian
  bullseye, whose PGDG apt repo is gone, so pgvector cannot be installed on it.
- `infra/db/init/10-test-database.sql` creates `campus_test` on first start.
- The PostGIS image installs extra extensions (tiger geocoder, topology) with their own
  tables. `infra/alembic/env.py` compares only tables defined in `agent/db.py`.
- `agent/db.py` and `infra/alembic/versions/` must agree;
  `tests/test_migrations.py::test_models_match_migration` and `alembic check` enforce it.
- Receipts (migration 0001): row triggers reject UPDATE/DELETE, statement triggers
  reject TRUNCATE, on both `receipts` and `anchors`. A BEFORE INSERT trigger takes an
  advisory lock, checks `previous_hash` equals the current tip (`'genesis'` when empty),
  then assigns `seq` from `receipts_seq`, so seq order equals chain order. Explicit `seq`
  values are overwritten. Anchors must match an existing receipt's `content_hash`.
  Migration 0001 refuses to downgrade.
- `repairs.embedding` is `vector(1024)` (Voyage default size). A different embedding
  size needs a migration.

## Campus data

- `campus_data/campus.yaml` (buildings, timezone, weather point, walking-minute matrix),
  `buildings/<CODE>.yaml` (floors, rooms), `assets/<CODE>.yaml` (equipment), `crews.yaml`.
  Schemas: `agent/campus/schema.py`.
- IDs: building `CSE`; floor `CSE-F3` / `CSE-FB`; room `CSE-3204`; equipment written out
  in full with the building prefix (`CSE-AHU-3`, `CSE-Z3S1`, `CSE-PNL-3-C04`, `SSB-CH-1`).
- Edges come from equipment: `upstream` (served_by from this asset), `serves` (served_by
  from each listed room or floor to this asset), `part_of`. Allowed type pairs are in
  `agent/campus/validate.py::ALLOWED_EDGES`.
- The central plant (2 chillers, 2 boilers) sits in the SSB basement. Loop A (CH-1,
  BLR-1) serves CSE and LEC; loop B (CH-2, BLR-2) serves SSB and LIB; REC uses CH-2 and
  its own boiler; CHEM has its own chiller and steam boiler.
- Demo rooms: CSE-3204 (zone 3S1), CSE-3210 (zone 3S2) and CSE-3100H (zone 3N1) share
  only CSE-AHU-3. CHEM-2108 is the organic lab for the "I smell gas in 2108" demo; no
  other building has a room 2108.
- The YAML was drafted by a one-off script on 2026-10-27 and is hand-maintained from now on.
- Upstream queries: `agent/campus/graph.py` (NetworkX) and `agent/campus/store.py`
  (recursive SQL) must agree; `tests/test_campus_store.py` checks every room in every
  domain. Domains: hvac, electrical, plumbing, elevator (elevator walks start at the
  room's floor). Symptom-to-domain map: `agent/intake/symptoms.py`.
- Schedule: Fall 2026, instruction Sep 24-Dec 4, finals Dec 5-11, holidays Nov 11 and
  Nov 26-27. Course titles are unique across the campus.
- `campus_data/failure_patterns.yaml` is a DRAFT until Kavya approves it; then it is
  frozen (it also drives the SimPy failure model). `repair_history.jsonl` is generated
  from it and committed; `pattern_id` is ground truth and never shown to the agent.

## Vendored ICE V.01

- `third_party/ice/`, from https://github.com/Dhammerr6304/ice-v01 at commit
  `8b7a2c14ac27ba48433f8731a5a3bd8273acb915` (2026-10-23). MIT license kept. Upstream's
  `receipts/` folder (their own data) is not copied.
- Never edit these files. `third_party/ice.sha256` plus `tests/test_vendored_ice.py`
  catch any change. Signing, Postgres persistence, verify and anchoring go in
  `agent/evidence/`.
- V.01 facts: `InferenceReceipt` is a dataclass; `content_hash` is SHA-256 of canonical
  JSON (sorted keys, compact separators) excluding `content_hash` and `signature`; empty
  `result_hash`/`extensions` are dropped from the dict before hashing; the first receipt's
  `previous_hash` is `"genesis"`; `sign_placeholder` is a plain SHA-256, not a signature;
  `verify_chain` checks links and hashes only.

## Data sources (details in docs/data-sources.md)

- SF 311: `vw6y-z8j6` on `data.sf.gov` (`data.sfgov.org` redirects there). PDDL. Crawl-delay 1.
  No reporter free text; staff `status_notes` carry duplicate labels.
- San Jose 311: yearly CSVs on `data.sanjoseca.gov`, CC0. robots disallows `/api/` and
  `/datastore/`, so use the dataset-page download links only. Crawl-delay 10. Ten
  columns, no free text, no closed date, no duplicate field.
- NWS: `api.weather.gov/robots.txt` is `Disallow: /` for all agents. Not used (decided
  2026-10-27); weather is simulated.
