# Progress

Dated log of finished work. Newest first.

## 2026-10-27: Nov 28 phase, part 1 (waiting on CONTACT_EMAIL and Anthropic credentials)

- Kavya's answers recorded: plan amendments in `docs/PLAN.md` (structure-only 311 tests,
  persona-written report text labelled simulation, simulated weather, personas written
  before Nov 30), README and `docs/data-sources.md` updated. First four commits pushed;
  CI run 1 passed.
- `data311/http.py`: the only way to fetch from a portal. Requires `CONTACT_EMAIL`,
  checks robots.txt, honours crawl delay, caches each file once with metadata, stops on
  401/403/429.
- `data311/sf.py` (category summary, per-category paged pull limited to approved
  categories), `data311/sj.py` (yearly CSV download, header check),
  `data311/duplicates.py` (SF DUPLICATE markers, original-case resolution,
  structure-only candidate pairs), `data311/trades.py` + `trade_map.yaml` (unapproved).
- Asset graph into Postgres (`agent/campus/store.py`) and upstream / served-rooms
  queries (`agent/campus/graph.py`); SQL and NetworkX agree for all 303 rooms in all
  four domains.
- Fall 2026 schedule generator: 9,615 blocks (8,694 classes, 305 finals, 616 events),
  deterministic, no double bookings, loads into `schedule_blocks`.
- Draft failure patterns (33 patterns over 9 asset types including room fixtures, 4 chronic assets) and a
  624-job repair history skeleton; the Claude note writer is written and tested with a
  stub but not run (no credentials in this environment).
- Tests: 99 passing.
- Blocked: SF category aggregate, the other-cities check and both 311 downloads (no
  `CONTACT_EMAIL`); repair notes and the review sample (no Anthropic credentials).

## 2026-10-27: Nov 25 phase (setup, campus model)

Done ahead of the calendar date; the plan's Nov 25 work list, minus the pilot emails.

- Repo skeleton in the plan's layout; `pyproject.toml` with exact pins (uv, Python 3.12);
  `web/package.json` and lockfile with exact pins.
- `infra/docker-compose.yml`: Postgres 17 + PostGIS 3.6 + pgvector 0.8.7 (custom image
  in `infra/db/`), api/worker/web placeholders. A `campus_test` database is created on
  first start.
- Alembic migration 0001: every table in the plan, the append-only receipts and anchors
  tables, and their triggers. SQLAlchemy models in `agent/db.py`.
- ICE V.01 vendored at `8b7a2c14ac27ba48433f8731a5a3bd8273acb915`, with a hash manifest.
- Campus YAML schemas (Pydantic, plus generated JSON Schema) and draft data: 6
  buildings, 303 rooms, 247 pieces of equipment, 8 technicians. Validator
  `python -m agent.campus.validate` loads everything into a NetworkX asset graph and
  checks that every room has an HVAC zone, air handler, plant, circuit and panel upstream.
- robots.txt checked for data.sf.gov, data.sanjoseca.gov and api.weather.gov; 311 fields
  inspected with small requests; `docs/data-sources.md` written.
- Tests: 40 passing (config, campus data and validator failure cases, vendored ICE,
  migration and append-only triggers against the test database).
- CI workflow `.github/workflows/ci.yml`: lint, mypy, campus validation, migration,
  tests, with Postgres started from the compose file.
- Open questions for Kavya: no free text in either 311 dataset; NWS robots.txt disallows
  everything; Socrata token needs an account; SF pull scope (years, categories).
