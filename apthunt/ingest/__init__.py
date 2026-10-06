"""
Listing ingestion — the seam between listing sources and the canonical DB.

A *listing source* is anything that can yield listings in the canonical
shape (see ``CANONICAL_FIELDS`` and "Listing ingestion" in the README).
Sources never touch the database. ``sync()`` owns the schema, the upsert,
freshness tracking (``first_seen_at`` / ``last_seen_at``), off-market
marking and the ``sync_log`` audit trail, so every source gets identical
semantics and the intelligence layer never has to care where a row came
from.

This repository ships exactly one source: ``sample``, a deterministic
generator of synthetic listings (``apthunt/ingest/sample.py``). To plug in
your own, subclass ``ListingSource``, yield canonical dicts from
``fetch()``, and register it with ``register_source``::

    from apthunt.ingest import ListingSource, register_source

    class MyFeed(ListingSource):
        name = "myfeed"

        def fetch(self):
            for row in my_rows():
                yield {
                    "source_id": row["id"],
                    "address": row["street"], "unit": row["apt"],
                    "neighborhood": row["area"], "borough": "brooklyn",
                    "lat": row["lat"], "lon": row["lon"],
                    "price": row["rent"], "beds": row["beds"],
                }

    register_source("myfeed", MyFeed)

then ``python3 ingest.py --source myfeed``.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

# ── Canonical schema ────────────────────────────────────────────

# Columns a source may populate. Anything else in a yielded dict is
# rejected loudly: a typo must not silently become a NULL column.
CANONICAL_FIELDS: tuple[str, ...] = (
    # provenance
    "source_id", "url",
    # location
    "address", "unit", "neighborhood", "borough", "zip", "lat", "lon",
    # cost
    "price", "net_effective_price", "no_fee", "months_free", "lease_term_months",
    # unit
    "beds", "baths", "sqft", "amenities", "pets_allowed", "furnished", "description",
    # media / availability
    "photos", "available_at",
    # contact
    "broker_name", "broker_firm", "broker_phone", "broker_email",
    # history (JSON list of {date, price, event}) and relist count
    "price_history", "relist_count",
    # building facts a source may know up front
    "building_year", "building_stories",
    # the source's own payload, kept for provenance. The deal scorer also
    # reads three optional keys from it: price_delta (dollars, negative =
    # cut), price_changed_at (ISO date) and months_free.
    "raw_json",
)

REQUIRED_FIELDS: tuple[str, ...] = ("source_id", "price", "beds", "lat", "lon")

# Backfill only. Freshness runs on our clock: first_seen_at is the time of
# the sync that first saw a listing. A source that replays history (an
# archive import, the sample generator) may supply first_seen_at instead;
# it is honoured once, on insert, and never moves afterwards. Live sources
# should leave it out.
BACKFILL_FIELDS: tuple[str, ...] = ("first_seen_at",)

# Fields stored as JSON text; lists/dicts are serialized on the way in.
_JSON_FIELDS = frozenset({"amenities", "photos", "price_history", "raw_json"})

# Managed by sync(), never by a source.
_MANAGED_FIELDS = ("id", "source", "first_seen_at", "last_seen_at", "status")

# One REAL column per scoring dimension. The scoring engine adds its own
# columns on first run too; creating the score columns up front lets the
# API sort and filter on dimensions that have not been scored yet (they
# simply read as "no data").
SCORE_DIMENSIONS: tuple[str, ...] = (
    "deal", "unit_amenities", "transit", "crime", "noise",
    "building_violations", "parks", "schools", "management", "convenience",
    "shelter", "pest", "greenery", "bedbug", "street_danger", "air_quality",
    "road_exposure", "flood_risk", "rent_stabilized",
)

_LISTINGS_DDL = """
CREATE TABLE IF NOT EXISTS listings (
    id                  TEXT PRIMARY KEY,
    source              TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    url                 TEXT,

    address             TEXT,
    unit                TEXT,
    neighborhood        TEXT,
    borough             TEXT,
    zip                 TEXT,
    lat                 REAL,
    lon                 REAL,

    price               INTEGER,
    net_effective_price INTEGER,
    no_fee              INTEGER,
    months_free         REAL,
    lease_term_months   INTEGER,

    beds                INTEGER,
    baths               REAL,
    sqft                INTEGER,
    amenities           TEXT,
    pets_allowed        INTEGER,
    furnished           INTEGER,
    description         TEXT,

    photos              TEXT,
    available_at        TEXT,

    broker_name         TEXT,
    broker_firm         TEXT,
    broker_phone        TEXT,
    broker_email        TEXT,

    price_history       TEXT,
    relist_count        INTEGER,
    building_year       INTEGER,
    building_stories    INTEGER,

    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    status              TEXT,

    raw_json            TEXT,
    UNIQUE(source, source_id)
)
"""

_SYNC_LOG_DDL = """
CREATE TABLE IF NOT EXISTS sync_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source            TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    total_count       INTEGER,
    listings_upserted INTEGER,
    status            TEXT,
    error             TEXT
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_listings_neighborhood ON listings(neighborhood)",
    "CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(price)",
    "CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status)",
    "CREATE INDEX IF NOT EXISTS idx_listings_beds ON listings(beds)",
    "CREATE INDEX IF NOT EXISTS idx_listings_last_seen ON listings(last_seen_at)",
    "CREATE INDEX IF NOT EXISTS idx_listings_source ON listings(source)",
)


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the canonical tables if missing. Safe to call repeatedly."""
    conn.execute(_LISTINGS_DDL)
    conn.execute(_SYNC_LOG_DDL)
    for ddl in _INDEXES:
        conn.execute(ddl)

    existing = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
    # Older databases predate the history/building columns.
    for col, col_type in (
        ("price_history", "TEXT"),
        ("relist_count", "INTEGER"),
        ("building_year", "INTEGER"),
        ("building_stories", "INTEGER"),
    ):
        if col not in existing:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {col_type}")
            existing.add(col)
    for dim in SCORE_DIMENSIONS:
        col = f"{dim}_score"
        if col not in existing:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {col} REAL")
            existing.add(col)
    if "rent_stabilized" not in existing:
        conn.execute("ALTER TABLE listings ADD COLUMN rent_stabilized INTEGER")
    conn.commit()


# ── Source interface ────────────────────────────────────────────


class ListingSource(ABC):
    """A pluggable supplier of canonical listings.

    Contract:
      * ``name`` is stored in ``listings.source`` and keys the upsert
        together with each listing's ``source_id``.
      * ``fetch()`` yields one dict per currently-available listing, using
        ``CANONICAL_FIELDS`` keys. ``REQUIRED_FIELDS`` must be present.
      * A source reports what is on the market *now*. Anything it stops
        yielding is marked INACTIVE by ``sync()``.
    """

    name: str = ""

    @abstractmethod
    def fetch(self) -> Iterable[dict[str, Any]]:
        ...


_SOURCES: dict[str, Callable[..., ListingSource]] = {}


def register_source(name: str, factory: Callable[..., ListingSource]) -> None:
    """Make a source selectable by name (``ingest.py --source <name>``)."""
    _SOURCES[name] = factory


def available_sources() -> list[str]:
    return sorted(_SOURCES)


def get_source(name: str, **options: Any) -> ListingSource:
    """Instantiate a registered source."""
    try:
        factory = _SOURCES[name]
    except KeyError:
        raise ValueError(
            f"unknown listing source {name!r}; available: {', '.join(available_sources())}"
        ) from None
    return factory(**options)


# ── Sync orchestrator ───────────────────────────────────────────


def _normalize(listing: dict[str, Any], source_name: str) -> dict[str, Any]:
    unknown = set(listing) - set(CANONICAL_FIELDS) - set(BACKFILL_FIELDS)
    if unknown:
        raise ValueError(
            f"{source_name}: non-canonical field(s) {sorted(unknown)} "
            f"on listing {listing.get('source_id')!r}"
        )
    missing = [f for f in REQUIRED_FIELDS if listing.get(f) is None]
    if missing:
        raise ValueError(
            f"{source_name}: listing {listing.get('source_id')!r} is missing {missing}"
        )

    row: dict[str, Any] = {field: listing.get(field) for field in CANONICAL_FIELDS}
    row["source_id"] = str(row["source_id"])
    for field in _JSON_FIELDS:
        if row[field] is not None and not isinstance(row[field], str):
            row[field] = json.dumps(row[field])
    for field in ("no_fee", "pets_allowed", "furnished"):
        if row[field] is not None:
            row[field] = int(bool(row[field]))
    return row


_INSERT_COLUMNS = _MANAGED_FIELDS + CANONICAL_FIELDS
# On conflict everything a source supplies is refreshed; identity and
# first_seen_at are ours and survive.
_UPDATE_COLUMNS = tuple(c for c in CANONICAL_FIELDS if c != "source_id") + (
    "last_seen_at", "status",
)
# Building facts are also filled in by scorers (from PLUTO); a source that
# doesn't know them must not blank them out on every refresh.
_KEEP_IF_ABSENT = frozenset({"building_year", "building_stories"})
_UPSERT_SQL = (
    f"INSERT INTO listings ({', '.join(_INSERT_COLUMNS)}) "
    f"VALUES ({', '.join(':' + c for c in _INSERT_COLUMNS)}) "
    "ON CONFLICT(source, source_id) DO UPDATE SET "
    + ", ".join(
        f"{c} = COALESCE(excluded.{c}, listings.{c})" if c in _KEEP_IF_ABSENT
        else f"{c} = excluded.{c}"
        for c in _UPDATE_COLUMNS
    )
)


def sync(
    conn: sqlite3.Connection,
    source: ListingSource,
    *,
    now: str | None = None,
) -> dict[str, int]:
    """Pull every listing from ``source`` into the canonical DB.

    * new ``(source, source_id)`` pairs are inserted with ``first_seen_at = now``
    * known pairs are refreshed in place, keeping ``id`` and ``first_seen_at``
    * listings of this source that were *not* yielded are marked INACTIVE
    * one ``sync_log`` row records the run (including failures)

    ``now`` is an ISO-8601 timestamp; it defaults to the current UTC time
    and exists so runs can be reproduced.

    Returns ``{"seen", "inserted", "updated", "deactivated"}``.
    """
    if not source.name:
        raise ValueError("listing source has no name")
    init_schema(conn)
    now = now or datetime.now(timezone.utc).isoformat()

    log_id = conn.execute(
        "INSERT INTO sync_log (source, started_at, status) VALUES (?, ?, 'running')",
        (source.name, now),
    ).lastrowid
    conn.commit()

    known = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT source_id, id FROM listings WHERE source = ?", (source.name,)
        )
    }
    seen: set[str] = set()
    inserted = updated = 0

    try:
        for listing in source.fetch():
            row = _normalize(listing, source.name)
            sid = row["source_id"]
            if sid in seen:
                raise ValueError(f"{source.name}: duplicate source_id {sid!r}")
            seen.add(sid)
            if sid in known:
                updated += 1
            else:
                inserted += 1
            row.update(
                # uuid5 keeps ids stable if the database is rebuilt from scratch
                id=known.get(sid) or str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"apthunt:{source.name}:{sid}")
                ),
                source=source.name,
                first_seen_at=listing.get("first_seen_at") or now,
                last_seen_at=now,
                status="ACTIVE",
            )
            conn.execute(_UPSERT_SQL, row)

        # Freshness: whatever the source no longer reports is off the market.
        gone = [(known[sid],) for sid in known if sid not in seen]
        deactivated = conn.executemany(
            "UPDATE listings SET status = 'INACTIVE' "
            "WHERE id = ? AND UPPER(status) = 'ACTIVE'",
            gone,
        ).rowcount if gone else 0
        conn.execute(
            "UPDATE sync_log SET finished_at = ?, total_count = ?, "
            "listings_upserted = ?, status = 'success' WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), len(seen), inserted + updated, log_id),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.execute(
            "UPDATE sync_log SET finished_at = ?, status = 'error', error = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), str(exc)[:500], log_id),
        )
        conn.commit()
        raise

    return {
        "seen": len(seen),
        "inserted": inserted,
        "updated": updated,
        "deactivated": deactivated,
    }


# Built-in sources register themselves on import.
from apthunt.ingest import sample as _sample  # noqa: E402,F401
