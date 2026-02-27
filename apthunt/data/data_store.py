"""
DataStore — local bulk-data layer for all scoring datasets.

Downloads reference and incident datasets from NYC Open Data (SODA) and
MTA GTFS into local SQLite tables.  Scorers query these tables instead
of hitting remote APIs per-listing.

Architecture
------------
- Each dataset is defined by a ``DatasetDef`` (name, SODA ID, columns to
  keep, refresh cadence, optional SoQL filter).
- On first run (or manual ``--refresh``), data is paginated from SODA and
  inserted into a dedicated SQLite table.
- A ``_data_meta`` table tracks when each dataset was last refreshed.
- In **dev mode** (default), data is never auto-refreshed — only downloaded
  when the table is empty or you explicitly pass ``--force``.
- In **prod mode** (``APTHUNT_ENV=production``), ``refresh_stale()`` checks
  cadences and re-downloads anything overdue.
- Old data is **never deleted before new data lands** — the strategy is
  atomic replace (write to temp table, then swap).

Tables created
--------------
One per dataset, named ``ds_{dataset_name}``.  Plus ``_data_meta`` for
bookkeeping.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from sodapy import Socrata

log = logging.getLogger(__name__)

DOMAIN = "data.cityofnewyork.us"

# ---------------------------------------------------------------------------
# Dataset definitions
# ---------------------------------------------------------------------------

@dataclass
class DatasetDef:
    """Specification for a single downloadable dataset."""
    name: str                          # table will be ds_{name}
    soda_id: str                       # NYC Open Data 4×4 identifier (or "" for non-SODA)
    select: str                        # SoQL $select — columns to keep
    refresh_days: int                  # how often to re-download
    where: str = ""                    # optional SoQL $where filter
    geo_columns: list[str] = field(default_factory=list)  # lat/lon cols to index
    index_columns: list[str] = field(default_factory=list)  # extra cols to index
    page_size: int = 50_000           # rows per SODA request
    post_process: str = ""            # optional post-processing hook name
    source: str = "soda"              # "soda" or "overpass"


# All datasets we bulk-download
DATASETS: dict[str, DatasetDef] = {

    # ── Reference (small, change slowly) ─────────────────────────

    "parks": DatasetDef(
        name="parks",
        soda_id="enfh-gkve",
        select="name311, multipolygon",
        refresh_days=90,          # quarterly
        post_process="parks_centroid",  # compute centroid lat/lon from geometry
    ),

    "schools": DatasetDef(
        name="schools",
        soda_id="97mf-9njv",
        select="school_name, latitude, longitude, attendance_rate, pct_stu_safe",
        refresh_days=180,         # biannually (data refreshes each school year)
        geo_columns=["latitude", "longitude"],
    ),

    "pluto": DatasetDef(
        name="pluto",
        soda_id="64uk-42ks",
        select="bbl,address,yearbuilt,numfloors,unitsres,"
               "firm07_flag,pfirm15_flag,latitude,longitude,ownername",
        refresh_days=180,         # biannual MapPLUTO releases
        geo_columns=["latitude", "longitude"],
        index_columns=["ownername"],
    ),

    # ── Incident (large, change frequently) ──────────────────────

    "crime": DatasetDef(
        name="crime",
        soda_id="5uac-w243",
        select="cmplnt_num,cmplnt_fr_dt,law_cat_cd,latitude,longitude",
        refresh_days=7,           # weekly
        where="cmplnt_fr_dt > '{TWELVE_MONTHS_AGO}'",
        geo_columns=["latitude", "longitude"],
    ),

    "noise": DatasetDef(
        name="noise",
        soda_id="erm2-nwe9",
        select="unique_key,created_date,complaint_type,latitude,longitude",
        refresh_days=7,           # weekly
        where=(
            "complaint_type IN ("
            "'Noise - Residential','Noise - Street/Sidewalk',"
            "'Noise - Commercial','Noise - Vehicle','Noise - Park',"
            "'Rodent','HEAT/HOT WATER'"
            ") AND created_date > '{TWELVE_MONTHS_AGO}'"
        ),
        geo_columns=["latitude", "longitude"],
    ),

    "dob_violations": DatasetDef(
        name="dob_violations",
        soda_id="3h2n-5cm9",
        select="isn_dob_bis_viol,boro,block,lot,"
               "violation_type,violation_category,issue_date",
        refresh_days=30,          # monthly
        where="violation_category NOT LIKE '%Resolved%'",
    ),

    "hpd_complaints": DatasetDef(
        name="hpd_complaints",
        soda_id="ygpa-z7cr",
        select="complaint_id,bbl,received_date,major_category,"
               "minor_category,complaint_status",
        refresh_days=7,           # weekly
        where="received_date > '{TWELVE_MONTHS_AGO}'",
        index_columns=["bbl"],
    ),

    "amenities": DatasetDef(
        name="amenities",
        soda_id="",                # not a SODA dataset
        select="",
        refresh_days=90,          # quarterly — OSM changes slowly
        geo_columns=["lat", "lon"],
        source="overpass",
    ),
}


# ---------------------------------------------------------------------------
# Metadata table
# ---------------------------------------------------------------------------

_META_DDL = """
CREATE TABLE IF NOT EXISTS _data_meta (
    dataset     TEXT PRIMARY KEY,
    row_count   INTEGER NOT NULL DEFAULT 0,
    refreshed_at TEXT NOT NULL,
    elapsed_sec  REAL NOT NULL DEFAULT 0
)
"""


# ---------------------------------------------------------------------------
# DataStore
# ---------------------------------------------------------------------------

class DataStore:
    """
    Manages bulk-downloaded datasets in SQLite.

    Usage::

        ds = DataStore(conn)
        ds.download("parks")               # first-time or manual refresh
        ds.download("crime", force=True)    # re-download even if fresh
        ds.refresh_stale()                  # prod: refresh anything overdue
        rows = ds.query("pluto",
                        "latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?",
                        (40.70, 40.72, -74.01, -73.99))
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        app_token: Optional[str] = None,
    ):
        self._conn = conn
        self._app_token = app_token
        self._conn.execute(_META_DDL)
        self._conn.commit()

    # ------------------------------------------------------------------ public

    def download(
        self,
        name: str,
        *,
        force: bool = False,
        quiet: bool = False,
    ) -> dict:
        """
        Download a dataset if missing, stale, or ``force=True``.

        Returns dict with keys: downloaded (bool), rows, elapsed_sec.
        """
        ddef = DATASETS[name]
        table = f"ds_{name}"

        if not force and self._is_fresh(name, ddef.refresh_days):
            if not quiet:
                meta = self._get_meta(name)
                log.info(
                    "%s: fresh (%s rows, refreshed %s) — skipping",
                    name, meta["row_count"], meta["refreshed_at"],
                )
            return {"downloaded": False, "rows": 0, "elapsed_sec": 0}

        # Download into a staging table, then swap atomically
        staging = f"_staging_{name}"
        t0 = time.time()

        if ddef.source == "overpass":
            total = self._overpass_download(staging, quiet=quiet)
        else:
            where = ddef.where
            if "{TWELVE_MONTHS_AGO}" in where:
                cutoff = (datetime.now() - timedelta(days=365)).strftime(
                    "%Y-%m-%dT00:00:00"
                )
                where = where.replace("{TWELVE_MONTHS_AGO}", cutoff)

            total = self._paginated_download(
                ddef.soda_id,
                staging,
                select=ddef.select,
                where=where,
                page_size=ddef.page_size,
                quiet=quiet,
            )
        elapsed = time.time() - t0

        if total == 0:
            # Don't replace existing data with nothing
            self._conn.execute(f"DROP TABLE IF EXISTS [{staging}]")
            self._conn.commit()
            log.warning("%s: download returned 0 rows — keeping old data", name)
            return {"downloaded": False, "rows": 0, "elapsed_sec": elapsed}

        # Atomic swap
        self._conn.execute(f"DROP TABLE IF EXISTS [{table}]")
        self._conn.execute(f"ALTER TABLE [{staging}] RENAME TO [{table}]")

        # Post-processing hooks (e.g. compute derived columns)
        if ddef.post_process:
            self._run_post_process(ddef.post_process, table)

        # Build spatial indexes
        self._build_indexes(table, ddef.geo_columns)

        # Build extra indexes (non-geo columns)
        self._build_indexes(table, ddef.index_columns)

        # Update metadata
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO _data_meta "
            "(dataset, row_count, refreshed_at, elapsed_sec) "
            "VALUES (?, ?, ?, ?)",
            (name, total, now, round(elapsed, 1)),
        )
        self._conn.commit()

        if not quiet:
            log.info(
                "%s: downloaded %s rows in %.1fs",
                name, f"{total:,}", elapsed,
            )
        return {"downloaded": True, "rows": total, "elapsed_sec": elapsed}

    def refresh_stale(self, *, force: bool = False, quiet: bool = False) -> dict:
        """
        Download all datasets that are missing or overdue.

        In dev mode, only downloads if the table doesn't exist at all.
        In prod mode (APTHUNT_ENV=production), respects refresh_days.
        """
        env = os.environ.get("APTHUNT_ENV", "development")
        results = {}
        for name, ddef in DATASETS.items():
            if env == "development":
                # Dev: only download if table doesn't exist
                if self._table_exists(f"ds_{name}"):
                    results[name] = {"downloaded": False, "rows": 0, "elapsed_sec": 0}
                    continue
            results[name] = self.download(name, force=force, quiet=quiet)
        return results

    def ensure_downloaded(self, name: str, *, quiet: bool = False) -> None:
        """Ensure a dataset exists locally; download if not."""
        table = f"ds_{name}"
        if not self._table_exists(table):
            self.download(name, quiet=quiet)

    def query(
        self,
        dataset: str,
        where_clause: str = "",
        params: tuple = (),
        select: str = "*",
        limit: int = 0,
    ) -> list[dict]:
        """
        Query a local dataset table.

        Args:
            dataset:      dataset name (e.g. "pluto", "crime")
            where_clause: SQL WHERE (without the WHERE keyword)
            params:       bind parameters
            select:       columns
            limit:        max rows (0 = unlimited)
        """
        table = f"ds_{dataset}"
        sql = f"SELECT {select} FROM [{table}]"
        if where_clause:
            sql += f" WHERE {where_clause}"
        if limit:
            sql += f" LIMIT {limit}"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_bbox(
        self,
        dataset: str,
        lat: float,
        lon: float,
        delta: float = 0.0015,
        select: str = "*",
        lat_col: str = "latitude",
        lon_col: str = "longitude",
    ) -> list[dict]:
        """Convenience: query a bounding box around (lat, lon)."""
        where = (
            f"CAST({lat_col} AS REAL) BETWEEN ? AND ? "
            f"AND CAST({lon_col} AS REAL) BETWEEN ? AND ?"
        )
        return self.query(
            dataset,
            where,
            (lat - delta, lat + delta, lon - delta, lon + delta),
            select=select,
        )

    def query_circle(
        self,
        dataset: str,
        lat: float,
        lon: float,
        radius_m: float,
        select: str = "*",
        lat_col: str = "latitude",
        lon_col: str = "longitude",
    ) -> list[dict]:
        """
        Query rows within ``radius_m`` meters of (lat, lon).

        Uses a bbox pre-filter + exact Haversine post-filter.
        """
        # 1° latitude ≈ 111,320 m.  At NYC, 1° longitude ≈ 85,000 m.
        lat_delta = radius_m / 111_320
        lon_delta = radius_m / 85_000

        # Ensure geo columns are always included in SELECT for Haversine
        bbox_select = select
        if select != "*":
            sel_cols = {c.strip() for c in select.split(",")}
            missing = {lat_col, lon_col} - sel_cols
            if missing:
                bbox_select = select + ", " + ", ".join(missing)

        # Pre-filter: bbox
        candidates = self.query_bbox(
            dataset, lat, lon, delta=max(lat_delta, lon_delta),
            select=bbox_select, lat_col=lat_col, lon_col=lon_col,
        )

        # Post-filter: exact Haversine
        from haversine import haversine as _hav, Unit
        results = []
        for row in candidates:
            try:
                rlat = float(row[lat_col])
                rlon = float(row[lon_col])
            except (KeyError, TypeError, ValueError):
                continue
            d = _hav((lat, lon), (rlat, rlon), unit=Unit.METERS)
            if d <= radius_m:
                results.append(row)
        return results

    def status(self) -> list[dict]:
        """Return metadata for all datasets."""
        rows = self._conn.execute(
            "SELECT dataset, row_count, refreshed_at, elapsed_sec "
            "FROM _data_meta ORDER BY dataset"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            ddef = DATASETS.get(d["dataset"])
            if ddef:
                d["refresh_days"] = ddef.refresh_days
                d["stale"] = not self._is_fresh(d["dataset"], ddef.refresh_days)
            result.append(d)
        # Add missing datasets
        downloaded = {r["dataset"] for r in result}
        for name, ddef in DATASETS.items():
            if name not in downloaded:
                result.append({
                    "dataset": name,
                    "row_count": 0,
                    "refreshed_at": None,
                    "elapsed_sec": 0,
                    "refresh_days": ddef.refresh_days,
                    "stale": True,
                })
        return sorted(result, key=lambda r: r["dataset"])

    # ---------------------------------------------------------------- private

    def _paginated_download(
        self,
        soda_id: str,
        table: str,
        *,
        select: str,
        where: str,
        page_size: int,
        quiet: bool,
    ) -> int:
        """Download via paginated SODA queries into a staging table."""
        client = Socrata(DOMAIN, self._app_token, timeout=120)

        # Drop staging table if it exists from a previous failed run
        self._conn.execute(f"DROP TABLE IF EXISTS [{table}]")
        self._conn.commit()

        total = 0
        offset = 0
        table_created = False
        max_retries = 3

        try:
            while True:
                kwargs: dict = {
                    "select": select,
                    "limit": page_size,
                    "offset": offset,
                    "order": ":id",
                }
                if where:
                    kwargs["where"] = where

                # Retry with exponential back-off on transient failures
                for attempt in range(1, max_retries + 1):
                    try:
                        rows = client.get(soda_id, **kwargs)
                        break
                    except Exception as exc:
                        if attempt == max_retries:
                            raise
                        wait = 5 * attempt
                        log.warning(
                            "  SODA request failed (attempt %d/%d): %s — retrying in %ds",
                            attempt, max_retries, exc, wait,
                        )
                        time.sleep(wait)

                if not rows:
                    break

                if not table_created:
                    # Derive columns from the select clause so we don't miss
                    # any columns that happen to be all-NULL in the first batch.
                    if select and select != "*":
                        columns = [c.strip() for c in select.split(",")]
                    else:
                        columns = list(rows[0].keys())
                    col_defs = ", ".join(f"[{c}] TEXT" for c in columns)
                    self._conn.execute(
                        f"CREATE TABLE [{table}] ({col_defs})"
                    )
                    table_created = True

                # Bulk insert — serialize any dict/list values to JSON
                # Use the canonical column list from table creation
                placeholders = ", ".join("?" for _ in columns)
                col_names = ", ".join(f"[{c}]" for c in columns)

                def _val(v):
                    if isinstance(v, (dict, list)):
                        return json.dumps(v)
                    return v

                self._conn.executemany(
                    f"INSERT INTO [{table}] ({col_names}) VALUES ({placeholders})",
                    [tuple(_val(r.get(c)) for c in columns) for r in rows],
                )
                self._conn.commit()

                total += len(rows)
                offset += page_size

                if not quiet:
                    log.info(
                        "  ... %s rows downloaded so far", f"{total:,}"
                    )

                if len(rows) < page_size:
                    break  # last page

                time.sleep(0.3)  # courtesy delay

        finally:
            client.close()

        return total

    # Overpass (OpenStreetMap) bulk download

    _OVERPASS_URL = "https://overpass-api.de/api/interpreter"
    _OVERPASS_QUERY = """
[out:json][timeout:120];
(
  node["shop"="supermarket"](40.49,-74.26,40.92,-73.70);
  node["shop"="convenience"](40.49,-74.26,40.92,-73.70);
  node["amenity"="pharmacy"](40.49,-74.26,40.92,-73.70);
  node["leisure"="fitness_centre"](40.49,-74.26,40.92,-73.70);
  node["shop"="laundry"](40.49,-74.26,40.92,-73.70);
  node["amenity"="cafe"](40.49,-74.26,40.92,-73.70);
  node["amenity"="restaurant"](40.49,-74.26,40.92,-73.70);
);
out body;
"""

    def _overpass_download(self, table: str, *, quiet: bool) -> int:
        """Download all NYC amenity nodes from Overpass into *table*."""
        self._conn.execute(f"DROP TABLE IF EXISTS [{table}]")
        self._conn.execute(
            f"CREATE TABLE [{table}] "
            "(osm_id INTEGER, category TEXT, name TEXT, lat REAL, lon REAL)"
        )
        self._conn.commit()

        data = urllib.parse.urlencode({"data": self._OVERPASS_QUERY}).encode()
        req = urllib.request.Request(
            self._OVERPASS_URL, data=data,
            headers={"User-Agent": "AptHunt/1.0"},
        )
        if not quiet:
            log.info("amenities: requesting Overpass API (all NYC) ...")
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())

        rows: list[tuple] = []
        for el in body.get("elements", []):
            tags = el.get("tags", {})
            shop = tags.get("shop", "")
            amenity = tags.get("amenity", "")
            leisure = tags.get("leisure", "")

            if shop in ("supermarket", "convenience"):
                cat = "grocery"
            elif amenity == "pharmacy":
                cat = "pharmacy"
            elif leisure == "fitness_centre":
                cat = "gym"
            elif shop == "laundry":
                cat = "laundry"
            elif amenity in ("cafe", "restaurant"):
                cat = "dining"
            else:
                continue  # should not happen given the query, but be safe

            rows.append((
                el.get("id"),
                cat,
                tags.get("name", ""),
                el.get("lat"),
                el.get("lon"),
            ))

        self._conn.executemany(
            f"INSERT INTO [{table}] (osm_id, category, name, lat, lon) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        if not quiet:
            log.info("amenities: %s nodes downloaded", f"{len(rows):,}")
        return len(rows)

    def _build_indexes(self, table: str, geo_columns: list[str]):
        """Create indexes on geo columns for fast bbox queries."""
        for col in geo_columns:
            idx_name = f"idx_{table}_{col}"
            self._conn.execute(
                f"CREATE INDEX IF NOT EXISTS [{idx_name}] ON [{table}] ([{col}])"
            )
        self._conn.commit()

    def _is_fresh(self, name: str, refresh_days: int) -> bool:
        """Check if a dataset was refreshed within its cadence."""
        meta = self._get_meta(name)
        if not meta:
            return False
        try:
            refreshed = datetime.fromisoformat(meta["refreshed_at"])
            return datetime.now(timezone.utc) - refreshed < timedelta(days=refresh_days)
        except (ValueError, TypeError):
            return False

    def _get_meta(self, name: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM _data_meta WHERE dataset = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def _table_exists(self, table: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    def _run_post_process(self, hook: str, table: str):
        """Run a named post-processing hook on a freshly-downloaded table."""
        if hook == "parks_centroid":
            self._pp_parks_centroid(table)
        else:
            log.warning("Unknown post-process hook: %s", hook)

    def _pp_parks_centroid(self, table: str):
        """
        Compute centroid lat/lon from multipolygon GeoJSON for each park.

        Adds ``centroid_lat`` and ``centroid_lon`` columns and populates
        them with the average of all polygon vertices.  These are used
        for fast bbox pre-filtering in local queries.
        """
        # Add centroid columns
        try:
            self._conn.execute(f"ALTER TABLE [{table}] ADD COLUMN centroid_lat REAL")
            self._conn.execute(f"ALTER TABLE [{table}] ADD COLUMN centroid_lon REAL")
        except sqlite3.OperationalError:
            pass  # columns already exist

        rows = self._conn.execute(
            f"SELECT rowid, multipolygon FROM [{table}]"
        ).fetchall()

        for row in rows:
            rowid = row[0]
            mp_raw = row[1]
            if not mp_raw:
                continue

            try:
                geom = json.loads(mp_raw) if isinstance(mp_raw, str) else mp_raw
            except (json.JSONDecodeError, TypeError):
                continue

            coords = _extract_all_coords(geom)
            if not coords:
                continue

            avg_lon = sum(c[0] for c in coords) / len(coords)
            avg_lat = sum(c[1] for c in coords) / len(coords)

            self._conn.execute(
                f"UPDATE [{table}] SET centroid_lat=?, centroid_lon=? WHERE rowid=?",
                (avg_lat, avg_lon, rowid),
            )

        # Index on centroids
        self._conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_clat ON [{table}] (centroid_lat)"
        )
        self._conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_clon ON [{table}] (centroid_lon)"
        )
        self._conn.commit()
        log.info("Parks: computed centroids for %d rows", len(rows))


def _extract_all_coords(geom: dict) -> list[tuple[float, float]]:
    """Extract all [lon, lat] vertices from a GeoJSON geometry."""
    gtype = geom.get("type", "")
    raw = geom.get("coordinates", [])
    points: list[tuple[float, float]] = []

    if gtype == "MultiPolygon":
        for polygon in raw:
            for ring in polygon:
                points.extend((c[0], c[1]) for c in ring)
    elif gtype == "Polygon":
        for ring in raw:
            points.extend((c[0], c[1]) for c in ring)
    elif gtype == "Point":
        if len(raw) >= 2:
            points.append((raw[0], raw[1]))

    return points
