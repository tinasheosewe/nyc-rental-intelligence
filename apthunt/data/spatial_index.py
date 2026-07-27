"""
In-memory spatial + keyed fast paths for scoring-time queries (Tier-1 perf).

Four classes, all optional accelerators over the SQLite tables that
``DataStore`` owns.  The SQLite path remains the correctness reference
and the automatic fallback — nothing here is load-bearing for
correctness, and every consumer degrades to SQLite when numpy/scipy are
unavailable or a load fails (see ``DataStore.attach_fast_path``).

MemoryIndex
-----------
Lazy per-dataset column store + cKDTree.  ``query_circle`` reproduces
``DataStore.query_circle`` semantics EXACTLY, including its quirks:

- the SQLite path pre-filters with a *degree-space* bbox of half-width
  ``max(radius/111320, radius/85000)`` and then post-filters with the
  ``haversine`` package (spherical, R = 6371.0088 km).  We replicate
  both filters, so even the sliver of points near the due-E/W circle
  boundary that the SQLite bbox drops is dropped identically here.
- each returned dict carries the requested columns PLUS the geo columns
  (the SQLite path appends them to SELECT for the Haversine test) plus
  ``"_dist_m"`` (true Haversine, same formula/radius as the package).

The KD-tree lives in equirectangular-projected meters
(y = lat*111320, x = lon*85000 — the constants used across the code
base).  At NYC latitudes that projection overestimates distances by at
most ~1.1% (lon scale at the north edge), so the tree is queried with a
1.5% + 2 m slack radius and the exact bbox+Haversine masks trim the
candidates — the projection error therefore never changes results, only
the (tiny) candidate superset.

KeyedMaps
---------
Lazy per-(dataset, key_expr) hash maps for exact-key lookups (BBL,
boro/block/lot, owner name …), replacing the per-building SQLite
round-trips of the building-dimension scorers with O(1) dict lookups.
``DataStore.rows_by_key`` consults an attached instance first and falls
back to the equivalent parameterized SQL built from the SAME recipe
(``data_store.keyed_where``), so both paths return identical row dicts.
The scorers use only exact-equality recipes, making the in-memory
answer bit-identical to the SQL it replaced: keys are the raw stored
values, rows with a NULL key component are unindexed (SQL ``=`` never
matches NULL), and the returned dicts carry the same column projection
the old SELECT produced.  Parity is asserted by
``scripts/bench_fast_path.py``.

BlockerRaster / SeveranceRaster
-------------------------------
10 m boolean/uint8 grids over the NYC bbox replacing the per-sample
SQLite probes in ``road_exposure._occluding_rows`` and
``utils.path_severance_penalty_m`` with O(1) array lookups.  These are
deliberate ~cell-quantized approximations of the 18 m / 22 m circular
probes (point-marked centroids + a small binary dilation); the
line-walk, end-buffer, and consecutive-sample merge semantics are
reproduced exactly.  Agreement is measured by
``scripts/bench_fast_path.py``.

Thread-safety: none — these are built and used by the single-threaded
scoring process.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import sys
import time
from typing import Optional

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from apthunt.data.data_store import keyed_canon_factory, keyed_key_columns

log = logging.getLogger(__name__)

# Projection constants — MUST match the ones used across the code base
# (DataStore.query_circle, road_exposure, utils, generate_heatmap).
LAT_M = 111_320.0   # meters per degree latitude
LON_M = 85_000.0    # meters per degree longitude at NYC

# Effective spherical earth radius of the `haversine` package
# (AVG_EARTH_RADIUS_KM = 6371.0088) — verified: haversine((0,0),(0,180))
# / pi == 6_371_008.8 m.  Using the same radius makes _dist_m match the
# SQLite path to float precision.
EARTH_RADIUS_M = 6_371_008.8

# Tree query slack over the true radius: covers the worst-case
# equirectangular overestimate at the bbox latitudes (~1.1%) so no
# in-circle point can be missed before the exact filters run.
_TREE_SLACK = 1.015
_TREE_SLACK_M = 2.0

# NYC bbox for the rasters (matches generate_heatmap / Overpass bbox).
GRID_MIN_LAT, GRID_MAX_LAT = 40.49, 40.92
GRID_MIN_LON, GRID_MAX_LON = -74.27, -73.68
RASTER_CELL_M = 10.0


def _haversine_m(lat1: float, lon1: float,
                 lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Vectorized Haversine (meters) — identical formula/radius to the
    ``haversine`` package used by the SQLite path."""
    rlat1 = math.radians(lat1)
    rlon1 = math.radians(lon1)
    rlat2 = np.radians(lat2)
    rlon2 = np.radians(lon2)
    d = (np.sin((rlat2 - rlat1) * 0.5) ** 2
         + math.cos(rlat1) * np.cos(rlat2)
         * np.sin((rlon2 - rlon1) * 0.5) ** 2)
    return 2.0 * EARTH_RADIUS_M * np.arcsin(np.sqrt(d))


def _to_float_array(seq) -> np.ndarray:
    """Column → float64 array; unparseable/NULL values become NaN."""
    try:
        return np.asarray(seq, dtype=np.float64)   # None → nan (numpy)
    except (TypeError, ValueError):
        out = np.full(len(seq), np.nan, dtype=np.float64)
        for i, v in enumerate(seq):
            try:
                out[i] = float(v)
            except (TypeError, ValueError):
                pass
        return out


class _DatasetEntry:
    """Loaded arrays + tree for one dataset."""

    __slots__ = ("lat", "lon", "tree", "cols", "all_cols",
                 "lat_col", "lon_col", "n_rows")

    def __init__(self):
        self.lat = None          # float64, valid rows only
        self.lon = None
        self.tree = None         # cKDTree over (lat*LAT_M, lon*LON_M)
        self.cols = {}           # name -> object ndarray (valid rows)
        self.all_cols = False    # True when every table column is loaded
        self.lat_col = "latitude"
        self.lon_col = "longitude"
        self.n_rows = 0


class MemoryIndex:
    """Lazy in-memory KD-tree index over ``ds_<name>`` tables.

    Usage::

        idx = MemoryIndex(conn)
        store.attach_memory_index(idx)      # or attach_fast_path(...)
        # DataStore.query_circle now delegates automatically.

    ``query_circle`` returns ``None`` (→ caller falls back to SQLite)
    whenever it cannot serve the request: missing table/column, dataset
    over ``max_rows``, or a geo-column mismatch with an earlier load.
    """

    def __init__(self, conn: sqlite3.Connection,
                 default_max_rows: Optional[int] = 3_000_000):
        self._conn = conn
        self._default_max_rows = default_max_rows
        self._entries: dict = {}     # dataset -> _DatasetEntry
        self._unservable: set = set()  # datasets we refuse until invalidate()

    # ------------------------------------------------------------- public

    def load(
        self,
        dataset_name: str,
        columns=None,
        lat_col: str = "latitude",
        lon_col: str = "longitude",
        max_rows: Optional[int] = None,
    ) -> bool:
        """Pull ``ds_<dataset_name>`` fully into memory and build its tree.

        Args:
            columns: attribute columns to keep (list/set), or ``None``
                     for every table column.
            max_rows: memory guard — refuse (with a logged warning) when
                      the table exceeds this many rows.  Defaults to the
                      index-wide ``default_max_rows``.

        Returns True on success; False (after logging) when the dataset
        cannot be served (missing table/column, too large).  A False
        result is remembered until ``invalidate(dataset_name)``.
        """
        table = f"ds_{dataset_name}"
        limit = max_rows if max_rows is not None else self._default_max_rows
        t0 = time.time()
        try:
            table_cols = [
                r[1] for r in
                self._conn.execute(f"PRAGMA table_info([{table}])")
            ]
            if not table_cols:
                raise sqlite3.OperationalError(f"no such table: {table}")

            if columns is None:
                attr_cols = [c for c in table_cols
                             if c not in (lat_col, lon_col)]
                all_cols = True
            else:
                attr_cols = [c for c in dict.fromkeys(columns)
                             if c not in (lat_col, lon_col)]
                missing = [c for c in attr_cols + [lat_col, lon_col]
                           if c not in table_cols]
                if missing:
                    raise sqlite3.OperationalError(
                        f"{table}: no such column(s): {', '.join(missing)}"
                    )
                all_cols = False

            if limit is not None:
                n = self._conn.execute(
                    f"SELECT COUNT(*) FROM [{table}]"
                ).fetchone()[0]
                if n > limit:
                    log.warning(
                        "spatial_index: %s has %s rows > max_rows=%s — "
                        "leaving it on the SQLite path",
                        dataset_name, f"{n:,}", f"{limit:,}",
                    )
                    self._unservable.add(dataset_name)
                    return False

            fetch_cols = [lat_col, lon_col] + attr_cols
            sel = ", ".join(f"[{c}]" for c in fetch_cols)
            rows = self._conn.execute(
                f"SELECT {sel} FROM [{table}]"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            log.warning("spatial_index: cannot load %s (%s) — "
                        "SQLite path will serve it", dataset_name, exc)
            self._unservable.add(dataset_name)
            self._entries.pop(dataset_name, None)
            return False

        entry = _DatasetEntry()
        entry.lat_col, entry.lon_col = lat_col, lon_col
        entry.all_cols = all_cols

        if rows:
            columns_data = list(zip(*rows))     # C-level transpose
        else:
            columns_data = [[] for _ in fetch_cols]

        lat = _to_float_array(columns_data[0])
        lon = _to_float_array(columns_data[1])
        valid = np.isfinite(lat) & np.isfinite(lon)

        entry.lat = lat[valid]
        entry.lon = lon[valid]
        entry.n_rows = int(entry.lat.shape[0])
        for name, data in zip(attr_cols, columns_data[2:]):
            arr = np.empty(len(data), dtype=object)
            arr[:] = data
            entry.cols[name] = arr[valid]

        pts = np.column_stack((entry.lat * LAT_M, entry.lon * LON_M))
        entry.tree = cKDTree(pts)

        self._entries[dataset_name] = entry
        self._unservable.discard(dataset_name)
        log.info(
            "spatial_index: loaded %s — %s rows (%s valid), cols=%s, "
            "tree built in %.2fs",
            dataset_name, f"{len(rows):,}", f"{entry.n_rows:,}",
            "ALL" if all_cols else sorted(entry.cols), time.time() - t0,
        )
        return True

    def query_circle(
        self,
        dataset: str,
        lat: float,
        lon: float,
        radius_m: float,
        select_cols: str = "*",
        lat_col: str = "latitude",
        lon_col: str = "longitude",
    ):
        """Fast equivalent of ``DataStore.query_circle``.

        Returns a ``list[dict]`` with identical membership and values to
        the SQLite path, or ``None`` when this index cannot serve the
        request (caller must then use the SQLite path).
        """
        if dataset in self._unservable:
            return None

        if select_cols == "*" or not select_cols.strip():
            wanted = None
        else:
            wanted = [c.strip() for c in select_cols.split(",") if c.strip()]

        entry = self._entries.get(dataset)
        if entry is not None and (entry.lat_col != lat_col
                                  or entry.lon_col != lon_col):
            # Same dataset queried under a different geo pair — rare
            # enough that we just decline rather than double-load.
            return None

        needs_load = entry is None
        if entry is not None and not entry.all_cols:
            if wanted is None:
                needs_load = True
                load_cols = None
            else:
                missing = [c for c in wanted
                           if c not in entry.cols
                           and c not in (lat_col, lon_col)]
                if missing:
                    needs_load = True
                    load_cols = list(entry.cols) + missing
        if needs_load:
            if entry is None:
                load_cols = wanted
            elif wanted is None:
                load_cols = None
            if not self.load(dataset, load_cols,
                             lat_col=lat_col, lon_col=lon_col):
                return None
            entry = self._entries[dataset]

        # 1) KD-tree candidate superset (slack covers projection error).
        r_tree = radius_m * _TREE_SLACK + _TREE_SLACK_M
        idx = entry.tree.query_ball_point(
            (lat * LAT_M, lon * LON_M), r_tree
        )
        if not idx:
            return []
        idx = np.asarray(idx, dtype=np.intp)
        rlat = entry.lat[idx]
        rlon = entry.lon[idx]

        # 2) Exact degree-space bbox — replicates query_bbox's
        #    delta = max(lat_delta, lon_delta) pre-filter, same
        #    arithmetic so boundary behaviour is bit-identical.
        delta = max(radius_m / LAT_M, radius_m / LON_M)
        mask = ((rlat >= lat - delta) & (rlat <= lat + delta)
                & (rlon >= lon - delta) & (rlon <= lon + delta))

        # 3) Exact Haversine post-filter (same formula & radius as the
        #    `haversine` package used by the SQLite path).
        dist = _haversine_m(lat, lon, rlat, rlon)
        mask &= dist <= radius_m
        if not mask.any():
            return []

        keep = idx[mask]
        dkeep = dist[mask]
        klat = rlat[mask]
        klon = rlon[mask]

        out_cols = wanted if wanted is not None else list(entry.cols)
        col_arrays = [
            (c, entry.cols[c]) for c in out_cols
            if c not in (lat_col, lon_col)
        ]

        results = []
        for j, i in enumerate(keep):
            row = {c: arr[i] for c, arr in col_arrays}
            row[lat_col] = float(klat[j])
            row[lon_col] = float(klon[j])
            row["_dist_m"] = float(dkeep[j])
            results.append(row)
        return results

    def has(self, dataset: str) -> bool:
        """True when *dataset* is loaded and servable."""
        return dataset in self._entries

    def invalidate(self, dataset: str) -> None:
        """Drop a dataset (call after a mid-process refresh/re-download)."""
        self._entries.pop(dataset, None)
        self._unservable.discard(dataset)
        log.info("spatial_index: invalidated %s", dataset)


# ---------------------------------------------------------------------------
# KeyedMaps — exact-key lookup fast path (building dimensions)
# ---------------------------------------------------------------------------

def _intern_val(v):
    """``sys.intern`` short strings during keyed loads.

    Key parts and categorical values (violation classes, statuses,
    boro/block/lot strings, owner names) repeat across millions of rows;
    interning collapses each distinct string to one shared object.  Long
    strings (novdescription …) are mostly unique and skipped.
    """
    if type(v) is str and len(v) <= 64:
        return sys.intern(v)
    return v


class _KeyedEntry:
    """Loaded map for one (dataset, key_expr) pair."""

    __slots__ = ("cols", "col_index", "map", "n_rows", "n_keys", "all_cols")

    def __init__(self):
        self.cols = []        # fetched column names (key cols first)
        self.col_index = {}   # name -> tuple position
        self.map = {}         # canonical key -> list[tuple(values)]
        self.n_rows = 0       # indexed rows
        self.n_keys = 0
        self.all_cols = False  # True when every table column is loaded


class KeyedMaps:
    """Lazy in-memory hash maps over ``ds_<name>`` tables for exact-key
    lookups — the building-dimension analogue of :class:`MemoryIndex`.

    Motivation: the per-building queries inside the scoring loops of
    building_violations / management / bedbug / pest each round-trip
    SQLite per key, and several hit tables with NO key index at all
    (ds_dob_violations, ds_hpd_violations, ds_dob_permits,
    ds_hpd_litigations carry only geo indexes), so every lookup is a
    1-2M-row scan.  One sequential pass here builds
    ``dict[key] -> list[row-tuples]`` and answers in O(1).

    Usage::

        maps = KeyedMaps(conn)
        maps.load_keyed("dob_violations", "boro,block,lot",
                        ["violation_type"])          # eager (optional)
        store.attach_fast_path(..., keyed_maps=maps)
        # DataStore.rows_by_key now answers from memory.

    ``get`` returns ``None`` (→ caller falls back to SQLite) whenever
    the pair cannot be served: missing table/column, dataset over
    ``max_rows``, or a bad recipe.  Row dicts are identical to the SQL
    fallback's: same column projection, same values, NULL-keyed rows
    excluded on both sides.  Key semantics live in the shared recipe
    helpers (``data_store.keyed_canon_factory`` / ``keyed_where``) so
    the two paths can never drift.

    Thread-safety: none — built and used by the single-threaded scoring
    process.
    """

    def __init__(self, conn: sqlite3.Connection,
                 default_max_rows: Optional[int] = 3_000_000):
        self._conn = conn
        self._default_max_rows = default_max_rows
        self._entries: dict = {}      # (dataset, key_expr) -> _KeyedEntry
        self._unservable: set = set()  # pairs refused until invalidate()

    # ------------------------------------------------------------- public

    def load_keyed(
        self,
        dataset_name: str,
        key_expr: str,
        columns=None,
        max_rows: Optional[int] = None,
    ) -> bool:
        """Build the map for ``ds_<dataset_name>`` keyed by *key_expr*.

        Args:
            columns: attribute columns to keep alongside the key columns
                     (list/tuple), ``None`` for every table column, or an
                     empty list for key columns only (count-style use).
            max_rows: memory guard — refuse (with a logged warning) when
                      the table exceeds this many rows.  Defaults to the
                      instance-wide ``default_max_rows`` (~3M).

        Returns True on success; False (after logging) when the pair
        cannot be served.  A False result is remembered until
        ``invalidate(dataset_name)``.
        """
        table = f"ds_{dataset_name}"
        ek = (dataset_name, key_expr)
        limit = max_rows if max_rows is not None else self._default_max_rows
        t0 = time.time()
        try:
            key_cols = keyed_key_columns(key_expr)
            canon = keyed_canon_factory(key_expr)
            table_cols = [
                r[1] for r in
                self._conn.execute(f"PRAGMA table_info([{table}])")
            ]
            if not table_cols:
                raise sqlite3.OperationalError(f"no such table: {table}")

            if columns is None:
                fetch_cols = list(table_cols)
                all_cols = True
            else:
                extra = [c for c in dict.fromkeys(columns)
                         if c not in key_cols]
                fetch_cols = list(key_cols) + extra
                all_cols = False
            missing = [c for c in fetch_cols if c not in table_cols]
            if missing:
                raise sqlite3.OperationalError(
                    f"{table}: no such column(s): {', '.join(missing)}"
                )

            if limit is not None:
                n = self._conn.execute(
                    f"SELECT COUNT(*) FROM [{table}]"
                ).fetchone()[0]
                if n > limit:
                    log.warning(
                        "spatial_index: %s has %s rows > max_rows=%s — "
                        "keyed lookups stay on the SQLite path",
                        dataset_name, f"{n:,}", f"{limit:,}",
                    )
                    self._unservable.add(ek)
                    return False

            sel = ", ".join(f"[{c}]" for c in fetch_cols)
            cur = self._conn.execute(f"SELECT {sel} FROM [{table}]")
        except (sqlite3.OperationalError, ValueError) as exc:
            log.warning(
                "spatial_index: cannot load keyed %s by %s (%s) — "
                "SQLite path will serve it", dataset_name, key_expr, exc,
            )
            self._unservable.add(ek)
            self._entries.pop(ek, None)
            return False

        key_idx = [fetch_cols.index(c) for c in key_cols]
        mp: dict = {}
        n_total = 0
        n_indexed = 0
        for row in cur:
            n_total += 1
            vals = tuple(_intern_val(v) for v in row)
            kv = canon(tuple(vals[i] for i in key_idx))
            if kv is None:      # NULL/unnormalizable key — SQL = can't match
                continue
            n_indexed += 1
            bucket = mp.get(kv)
            if bucket is None:
                mp[kv] = [vals]
            else:
                bucket.append(vals)

        entry = _KeyedEntry()
        entry.cols = fetch_cols
        entry.col_index = {c: i for i, c in enumerate(fetch_cols)}
        entry.map = mp
        entry.n_rows = n_indexed
        entry.n_keys = len(mp)
        entry.all_cols = all_cols
        self._entries[ek] = entry
        self._unservable.discard(ek)
        log.info(
            "spatial_index: keyed %s by %s — %s rows (%s indexed, "
            "%s keys), cols=%s, built in %.2fs",
            dataset_name, key_expr, f"{n_total:,}", f"{n_indexed:,}",
            f"{len(mp):,}", "ALL" if all_cols else fetch_cols,
            time.time() - t0,
        )
        return True

    def get(self, dataset: str, key_expr: str, key, columns=None):
        """Rows for one exact key — fast equivalent of the SQL built by
        ``data_store.keyed_where``.

        Returns a ``list[dict]`` with identical membership/values to the
        SQLite fallback, or ``None`` when this map cannot serve the pair
        (caller must then use the SQLite path).  Missing columns trigger
        a lazy reload with the union of known + requested columns.
        """
        ek = (dataset, key_expr)
        if ek in self._unservable:
            return None

        entry = self._entries.get(ek)
        needs_load = entry is None
        load_cols = columns
        if entry is not None:
            if columns is None:
                if not entry.all_cols:
                    needs_load, load_cols = True, None
            else:
                missing = [c for c in columns if c not in entry.col_index]
                if missing:
                    needs_load = True
                    load_cols = (None if entry.all_cols
                                 else list(entry.cols) + missing)
        if needs_load:
            if not self.load_keyed(dataset, key_expr, load_cols):
                return None
            entry = self._entries[ek]

        canon = keyed_canon_factory(key_expr)
        vals = tuple(key) if isinstance(key, (list, tuple)) else (key,)
        kv = canon(vals)
        if kv is None:          # unmatchable probe — SQL side matches nothing
            return []
        rows = entry.map.get(kv)
        if not rows:
            return []
        out_cols = entry.cols if columns is None else list(
            dict.fromkeys(columns))
        pairs = [(c, entry.col_index[c]) for c in out_cols]
        return [{c: t[i] for c, i in pairs} for t in rows]

    def has(self, dataset: str, key_expr: Optional[str] = None) -> bool:
        """True when *dataset* (optionally under *key_expr*) is loaded."""
        if key_expr is not None:
            return (dataset, key_expr) in self._entries
        return any(ds == dataset for ds, _ in self._entries)

    def invalidate(self, dataset: str) -> None:
        """Drop every map for *dataset* (after a mid-process refresh)."""
        stale = [ek for ek in self._entries if ek[0] == dataset]
        for ek in stale:
            self._entries.pop(ek, None)
        self._unservable = {ek for ek in self._unservable
                            if ek[0] != dataset}
        if stale:
            log.info("spatial_index: invalidated keyed maps for %s", dataset)


# ---------------------------------------------------------------------------
# Rasters
# ---------------------------------------------------------------------------

def _disk(radius_cells: int) -> np.ndarray:
    """Boolean disk structuring element of the given cell radius."""
    y, x = np.ogrid[-radius_cells:radius_cells + 1,
                    -radius_cells:radius_cells + 1]
    return (x * x + y * y) <= radius_cells * radius_cells


class _RasterBase:
    """Shared 10 m grid geometry over the NYC bbox."""

    def __init__(self):
        self._lat_step = RASTER_CELL_M / LAT_M
        self._lon_step = RASTER_CELL_M / LON_M
        self._nrows = int(math.ceil(
            (GRID_MAX_LAT - GRID_MIN_LAT) / self._lat_step)) + 1
        self._ncols = int(math.ceil(
            (GRID_MAX_LON - GRID_MIN_LON) / self._lon_step)) + 1

    def _cell_indices(self, lats: np.ndarray, lons: np.ndarray):
        """(row_idx, col_idx, in_bounds_mask) for arrays of coordinates."""
        ri = np.floor((lats - GRID_MIN_LAT) / self._lat_step).astype(np.intp)
        ci = np.floor((lons - GRID_MIN_LON) / self._lon_step).astype(np.intp)
        ok = (ri >= 0) & (ri < self._nrows) & (ci >= 0) & (ci < self._ncols)
        return ri, ci, ok


class BlockerRaster(_RasterBase):
    """10 m boolean grid of 3+-story PLUTO lots (sightline occluders).

    Cells are marked at each qualifying lot centroid then dilated with a
    2-cell (~20 m) disk via ``scipy.ndimage.binary_dilation`` — the disk
    approximates ``road_exposure._lots_blocking``'s 18 m circular probe
    around the sample point (probe radius 18 m / cell 10 m ≈ 2 cells)
    plus a sliver of footprint spread.  ``occluding_rows`` reproduces
    the exact line-walk semantics of ``_occluding_rows``: 25 m sampling,
    20 m end buffers, ≤45 m short-circuit, consecutive blocked samples
    merged into one "row".
    """

    #: floors threshold — must match road_exposure.OCCLUDER_MIN_FLOORS
    MIN_FLOORS = 3.0

    def __init__(self, conn: sqlite3.Connection, dilate_cells: int = 2):
        super().__init__()
        t0 = time.time()
        rows = conn.execute(
            "SELECT latitude, longitude FROM ds_pluto "
            "WHERE CAST(numfloors AS REAL) >= ? "
            "AND latitude IS NOT NULL AND longitude IS NOT NULL",
            (self.MIN_FLOORS,),
        ).fetchall()
        pts = np.array([tuple(r) for r in rows], dtype=np.float64)
        grid = np.zeros((self._nrows, self._ncols), dtype=bool)
        n_marked = 0
        if pts.size:
            lats = _to_float_array(pts[:, 0])
            lons = _to_float_array(pts[:, 1])
            ri, ci, ok = self._cell_indices(lats, lons)
            grid[ri[ok], ci[ok]] = True
            n_marked = int(ok.sum())
        if dilate_cells > 0:
            grid = ndimage.binary_dilation(grid, structure=_disk(dilate_cells))
        self._grid = grid
        self.build_seconds = time.time() - t0
        log.info(
            "spatial_index: BlockerRaster %dx%d (10m) from %s lots, "
            "dilate=%d cells, built in %.2fs",
            self._nrows, self._ncols, f"{n_marked:,}", dilate_cells,
            self.build_seconds,
        )

    def blocked_at(self, lat: float, lon: float) -> bool:
        """Raster analogue of ``_lots_blocking`` for a single probe."""
        ri, ci, ok = self._cell_indices(
            np.asarray([lat]), np.asarray([lon]))
        return bool(ok[0] and self._grid[ri[0], ci[0]])

    def occluding_rows(self, lat1: float, lon1: float,
                       lat2: float, lon2: float) -> int:
        """Count intervening 3+-story building rows on the sightline.

        Same walk as ``road_exposure._occluding_rows``: samples every
        ~25 m (endpoints buffered 20 m), consecutive blocked samples
        count as one row.  Out-of-bbox samples read as unblocked.
        """
        dy = (lat2 - lat1) * LAT_M
        dx = (lon2 - lon1) * LON_M
        seg = math.hypot(dx, dy)
        if seg <= 45.0:
            return 0
        n = max(1, int(seg / 25.0))
        f = np.arange(1, n, dtype=np.float64) / n
        d_here = f * seg
        keep = (d_here >= 20.0) & ((seg - d_here) >= 20.0)
        if not keep.any():
            return 0
        fk = f[keep]
        plat = lat1 + fk * (lat2 - lat1)
        plon = lon1 + fk * (lon2 - lon1)
        ri, ci, ok = self._cell_indices(plat, plon)
        blocked = np.zeros(fk.shape[0], dtype=bool)
        blocked[ok] = self._grid[ri[ok], ci[ok]]
        # Buffer-skipped samples sit only at the contiguous ends, so
        # "rows" = count of False→True transitions in the kept run.
        return int(blocked[0]) + int(
            np.count_nonzero(blocked[1:] & ~blocked[:-1]))


class SeveranceRaster(_RasterBase):
    """10 m uint8 grid of the max severance penalty class per cell.

    Built from ``ds_roads`` points whose class appears in
    ``utils.SEVERANCE_PENALTY_M``; each class mask is dilated with a
    2-cell (~20 m) disk, approximating the 22 m circular probe of
    ``utils.path_severance_penalty_m`` (spec'd there as "~22 m effective
    radius ≈ dilation").  Cell values are codes into a penalty LUT so
    "max code" == "max penalty".  ``path_penalty_m`` reproduces the
    exact 20 m sampling and consecutive-encounter merge semantics.
    """

    def __init__(self, conn: sqlite3.Connection, dilate_cells: int = 2):
        super().__init__()
        from apthunt.scoring.utils import SEVERANCE_PENALTY_M
        t0 = time.time()

        penalties = sorted(set(SEVERANCE_PENALTY_M.values()))
        self._penalty_lut = np.array([0.0] + penalties, dtype=np.float64)
        code_of = {p: i + 1 for i, p in enumerate(penalties)}

        grid = np.zeros((self._nrows, self._ncols), dtype=np.uint8)
        classes = list(SEVERANCE_PENALTY_M)
        qmarks = ",".join("?" * len(classes))
        rows = conn.execute(
            f"SELECT lat, lon, road_class FROM ds_roads "
            f"WHERE road_class IN ({qmarks}) "
            f"AND lat IS NOT NULL AND lon IS NOT NULL",
            classes,
        ).fetchall()

        by_penalty: dict = {}
        for r in rows:
            p = SEVERANCE_PENALTY_M[r[2]]
            by_penalty.setdefault(p, []).append((r[0], r[1]))

        structure = _disk(dilate_cells) if dilate_cells > 0 else None
        n_pts = 0
        for p, pts in sorted(by_penalty.items()):
            arr = np.asarray(pts, dtype=np.float64)
            lats = _to_float_array(arr[:, 0])
            lons = _to_float_array(arr[:, 1])
            ri, ci, ok = self._cell_indices(lats, lons)
            mask = np.zeros_like(grid, dtype=bool)
            mask[ri[ok], ci[ok]] = True
            n_pts += int(ok.sum())
            if structure is not None:
                mask = ndimage.binary_dilation(mask, structure=structure)
            np.maximum(grid, np.uint8(code_of[p]) * mask, out=grid)

        self._grid = grid
        self.build_seconds = time.time() - t0
        log.info(
            "spatial_index: SeveranceRaster %dx%d (10m) from %s road "
            "points, dilate=%d cells, built in %.2fs",
            self._nrows, self._ncols, f"{n_pts:,}", dilate_cells,
            self.build_seconds,
        )

    def path_penalty_m(self, lat1: float, lon1: float,
                       lat2: float, lon2: float) -> float:
        """Pedestrian-severance penalty for the straight path — same
        semantics as ``utils.path_severance_penalty_m``: samples every
        ~20 m, each run of consecutive severed samples adds the penalty
        of the class encountered at the run's first sample."""
        dy = (lat2 - lat1) * LAT_M
        dx = (lon2 - lon1) * LON_M
        seg = math.hypot(dx, dy)
        if seg <= 40.0:
            return 0.0
        n = max(1, int(seg / 20.0))
        f = np.arange(1, n, dtype=np.float64) / n
        plat = lat1 + f * (lat2 - lat1)
        plon = lon1 + f * (lon2 - lon1)
        ri, ci, ok = self._cell_indices(plat, plon)
        codes = np.zeros(f.shape[0], dtype=np.uint8)
        codes[ok] = self._grid[ri[ok], ci[ok]]
        pen = self._penalty_lut[codes]
        prev = np.concatenate(([0.0], pen[:-1]))
        starts = (pen > 0.0) & (prev == 0.0)
        return float(pen[starts].sum())


# ---------------------------------------------------------------------------
# CLI activation
# ---------------------------------------------------------------------------

#: (dataset, key_expr, columns) preloaded by ``activate_fast_path`` —
#: exactly the per-building lookups the four building-dimension scorers
#: key into (building_violations, management, bedbug, pest; see each
#: scorer's ``rows_by_key`` call sites).  Datasets the scorers don't key
#: into (ecb_violations, hpd_registrations, hpd_registration_contacts,
#: omo_charges … — API-side lookups) are NOT preloaded; any future
#: ``rows_by_key`` touch loads them lazily instead.  Empty column tuples
#: mean "key columns only" (pure count callers).
SCORER_KEYED_PRELOADS = (
    # building_violations
    ("dob_violations", "boro,block,lot", ("violation_type",)),
    ("hpd_violations", "boroid,block,lot",
     ("class", "inspectiondate", "novdescription",
      "certifieddate", "violationstatus")),
    ("dob_permits", "borough,block,lot", ()),
    # management (pest reuses hpd_complaints/bbl)
    ("pluto", "ownername", ("bbl", "unitsres")),
    ("hpd_complaints", "bbl",
     ("major_category", "minor_category", "received_date")),
    ("evictions", "bbl", ("executed_date",)),
    ("hpd_litigations", "boroid,block,lot", ()),
    # bedbug (party-wall adjacency stays on SQLite — range scan on an
    # indexed bbl column, not an exact-key lookup)
    ("bedbug_reporting", "bbl",
     ("of_dwelling_units", "infested_dwelling_unit_count",
      "re_infested_dwelling_unit", "filing_date")),
)


def activate_fast_path(conn: sqlite3.Connection, store,
                       *, verbose: bool = True) -> bool:
    """Build MemoryIndex + rasters + KeyedMaps on *conn*, attach to *store*.

    Activation helper for the CLI entry points (``run_scores.py`` /
    ``scripts/build_baseline.py`` ``--fast``).  Each component is built
    independently — a missing table only disables that component, and a
    total failure leaves the store untouched (pure SQLite behaviour).
    Build times are logged; KD-trees load lazily per dataset and log
    their own load times as scoring first touches each dataset; keyed
    maps for the building-dimension scorers are preloaded eagerly (each
    logs rows/keys/seconds).

    Returns True when at least one component was attached.
    """
    if verbose and not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[fast] %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)

    t0 = time.time()
    index = MemoryIndex(conn)
    blocker = None
    severance = None
    try:
        blocker = BlockerRaster(conn)
    except Exception as exc:
        log.warning("BlockerRaster unavailable (%s) — occlusion stays "
                    "on the SQLite path", exc)
    try:
        severance = SeveranceRaster(conn)
    except Exception as exc:
        log.warning("SeveranceRaster unavailable (%s) — severance stays "
                    "on the SQLite path", exc)

    keyed = None
    n_keyed = 0
    try:
        keyed = KeyedMaps(conn)
        for ds, key_expr, cols in SCORER_KEYED_PRELOADS:
            try:
                load_cols = list(cols)
                if load_cols:
                    # Tolerate schema drift (columns landing with a
                    # re-download in flight): preload only the columns
                    # that exist today; the lazy path upgrades later.
                    have = {r[1] for r in conn.execute(
                        f"PRAGMA table_info([ds_{ds}])")}
                    load_cols = [c for c in load_cols if c in have]
                if keyed.load_keyed(ds, key_expr, load_cols):
                    n_keyed += 1
            except Exception as exc:
                log.warning("keyed preload %s by %s failed (%s) — "
                            "SQLite path will serve it", ds, key_expr, exc)
    except Exception as exc:
        keyed = None
        log.warning("KeyedMaps unavailable (%s) — per-key lookups stay "
                    "on the SQLite path", exc)

    store.attach_fast_path(index, blocker, severance, keyed_maps=keyed)
    log.info(
        "fast path attached in %.1fs (rasters: blocker %s, severance %s; "
        "keyed maps: %d/%d preloaded; KD-trees load lazily per dataset)",
        time.time() - t0,
        f"{blocker.build_seconds:.1f}s" if blocker is not None else "OFF",
        f"{severance.build_seconds:.1f}s" if severance is not None else "OFF",
        n_keyed, len(SCORER_KEYED_PRELOADS),
    )
    return True
