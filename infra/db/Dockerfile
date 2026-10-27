# Postgres 17 with PostGIS 3.6 (official postgis image, Debian 13) plus pgvector from the PGDG apt repo.
# Base image pinned by digest; pgvector pinned to an exact package version.
FROM postgis/postgis:17-3.6@sha256:1ac10b2551ba84ae05db8e7da477afcd0a3fede99bcb7705da2ee157390cb095

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-17-pgvector=0.8.7-1.pgdg13+1 \
    && rm -rf /var/lib/apt/lists/*

COPY init/ /docker-entrypoint-initdb.d/
