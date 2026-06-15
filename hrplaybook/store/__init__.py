"""Relational store (minimal production slice).

The current app uses out/<date>/*.csv as its database. This package is the first
step of the documented production migration: a typed relational store that ingests
a built slate and serves it via queries instead of re-parsing CSV per request.

Runnable today on stdlib sqlite3 (schema is SQLite-compatible); the production
DDL (Postgres, with jsonb/partitioning) lives in db/schema.postgres.sql. The web
layer can switch to this store without touching the pure scoring engines.
"""
from .repo import connect, get_slate, init_db, ingest_slate, list_dates  # noqa: F401
