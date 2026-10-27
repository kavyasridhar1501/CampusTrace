# CampusTrace

Multi-agent facilities triage that traces building complaints to the failing equipment,
schedules the fix, and checks it stayed fixed.

Work in progress. The build plan is in [docs/PLAN.md](docs/PLAN.md) and the log of
finished work in [docs/PROGRESS.md](docs/PROGRESS.md).

## What is real and what is simulated

Every result in this README will carry one of three labels:

- **311**: measured on real San Francisco 311 service requests, using structure only
  (category, location, timing, time to close, the city's own duplicate markers). Neither
  San Francisco nor San Jose publishes the reporter's own words, so no 311 result is a
  test on report text. San Jose data shapes the category and timing mix only. Details in
  [docs/data-sources.md](docs/data-sources.md).
- **simulation**: measured on the simulated campus (buildings, equipment, schedules,
  crews, weather and repair history are invented), in the SimPy month-long simulation,
  or on report text written by the persona simulator. Trade assignment and text-based
  duplicate detection are simulation results.
- **pilot**: measured on real reports from a pilot with a campus department, if one happens.

No results yet.

## Running it

```
uv sync
docker compose -f infra/docker-compose.yml up -d --wait db
uv run alembic -c infra/alembic.ini upgrade head
uv run python -m agent.campus.validate
uv run pytest
```

Copy `.env.example` to `.env` and fill in the keys you need.

## Credits

The decision ledger is built on [ICE V.01 (Immutable Chain Evidence)](https://github.com/Dhammerr6304/ice-v01)
by REKT☆WRLD Labs / Dhammerr6304, MIT license. It is vendored unmodified in
`third_party/ice/` at commit `8b7a2c14ac27ba48433f8731a5a3bd8273acb915`; see
[third_party/README.md](third_party/README.md).

311 data: San Francisco 311 Cases (DataSF, PDDL 1.0) and San Jose 311 Service Request
Data (City of San José, CC0).
