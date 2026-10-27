# Progress

Dated log of finished work. Newest first.

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
