# Block Quality Score — Data Feasibility & Design

## Summary

The Block Quality Score is a composite numeric rating (0–100) for the
immediate micro-neighborhood surrounding a listing. It answers the
question renters actually care about: **"What is it like to live on
this specific block?"**

All data required to compute the score is freely available through
NYC Open Data (Socrata SODA API) and MTA GTFS feeds. No authentication
is required; a free app token raises rate limits from 1,000 to 50,000
requests per hour. Every source listed below has been live-tested and
confirmed returning data as of February 2026.

---

## Data Sources

### 1. NYPD Crime Complaints (Current Year-to-Date)

| Key | Value |
|-----|-------|
| Endpoint | `https://data.cityofnewyork.us/resource/5uac-w243.json` |
| Records | ~580K |
| Update Frequency | Quarterly |
| Geo Support | `within_circle(geocoded_column, lat, lon, radius_m)` |
| Key Fields | `law_cat_cd` (FELONY / MISDEMEANOR / VIOLATION), `ofns_desc`, `cmplnt_fr_dt` |

Provides crime severity and type within a configurable radius of any
lat/lon. Use severity classes as multipliers when scoring.

### 2. 311 Service Requests

| Key | Value |
|-----|-------|
| Endpoint | `https://data.cityofnewyork.us/resource/erm2-nwe9.json` |
| Records | Tens of millions |
| Update Frequency | Daily |
| Geo Support | `within_circle(location, lat, lon, radius_m)` |
| Key Fields | `complaint_type`, `descriptor`, `created_date` |

Complaint types include `Noise - Residential`, `Rodent`, `HEAT/HOT WATER`,
`Illegal Parking`, `Unsanitary Condition`, etc. Serves as a direct
quality-of-life proxy block-by-block. Filter to the last 12 months for
relevance.

### 3. PLUTO (Primary Land Use Tax Lot Output)

| Key | Value |
|-----|-------|
| Endpoint | `https://data.cityofnewyork.us/resource/64uk-42ks.json` |
| Records | ~858K tax lots |
| Update Frequency | Annual |
| Geo Support | Has `latitude` / `longitude` columns |
| Key Fields | `bbl`, `address`, `yearbuilt`, `numfloors`, `unitsres`, `zonedist1`, `assesstot`, `firm07_flag`, `pfirm15_flag` |

**The single most valuable dataset.** One query returns building age,
floor count, residential unit count, zoning district, assessed value,
and FEMA flood zone flags (`firm07_flag` for 2007 FIRM maps,
`pfirm15_flag` for 2015 preliminary maps). PLUTO also provides the BBL
(Borough-Block-Lot) key needed to join DOB violations, collapsing
building quality and flood risk into a single source.

### 4. DOB Violations

| Key | Value |
|-----|-------|
| Endpoint | `https://data.cityofnewyork.us/resource/3h2n-5cm9.json` |
| Records | ~2.5M |
| Update Frequency | Daily |
| Geo Support | **None** — join to PLUTO via `boro` + `block` + `lot` → BBL |
| Key Fields | `violation_type`, `violation_category`, `description`, `issue_date`, `disposition_date` |

Violation types include elevator, structural, fire egress. Active
violations with no disposition indicate unresolved building issues.
Must be joined to PLUTO by BBL to get lat/lon for radius queries.

### 5. DOB Job Filings & Permits

| Key | Value |
|-----|-------|
| Endpoint | `https://data.cityofnewyork.us/resource/ic3t-wcy2.json` |
| Records | ~2.7M |
| Update Frequency | Daily |
| Geo Support | Has lat/lon |
| Key Fields | `job_type`, `job_status`, `filing_date`, `job_description` |

Recent construction and renovation permits serve as a
gentrification/investment signal. A cluster of new-building or
alteration permits in a 400m radius indicates an area trending upward.

### 6. NYC Parks Properties

| Key | Value |
|-----|-------|
| Endpoint | `https://data.cityofnewyork.us/resource/enfh-gkve.json` |
| Records | All NYC parks |
| Update Frequency | Periodic |
| Geo Support | `multipolygon` geometry column |
| Key Fields | `signname`, `typecategory` (Neighborhood Park, Playground, Triangle/Plaza), `acres` |

Full polygon boundaries for every park. Use locally with Shapely for
point-in-polygon or nearest-park distance calculations (Socrata does
not support spatial intersection queries natively). Download once and
cache — parks don't move.

### 7. MTA Subway Stations (GTFS)

| Key | Value |
|-----|-------|
| Source | `http://web.mta.info/developers/data/nyct/subway/google_transit.zip` |
| Records | ~472 stations |
| Format | GTFS `stops.txt` (CSV with `stop_lat`, `stop_lon`, `stop_name`) |
| Routes | Cross-reference with `routes.txt` and `stop_times.txt` |

Static file. Download once, parse `stops.txt` for station lat/lon.
Count stations within 800m (~10 minute walk) and weight by number of
routes served. A station serving 4 lines is worth more than one
serving 1.

---

## Score Components

| Component | Weight | Source(s) | Radius | Method |
|-----------|--------|-----------|--------|--------|
| **Crime Safety** | 25% | NYPD Complaints | 400m | Count complaints in radius, weight by severity (felony ×3, misdemeanor ×1.5, violation ×1). Normalize against city-wide median for same radius. Invert so lower crime = higher score. |
| **Noise & Quality of Life** | 15% | 311 Requests | 300m | Filter to noise, rodent, sanitation, heat/hot-water complaint types. Count in last 12 months. Compare to city baseline. |
| **Building Quality** | 15% | PLUTO + DOB Violations | Per-building | `yearbuilt` for age score, active DOB violation count (join via BBL), `numfloors` as a density signal. |
| **Transit Access** | 15% | MTA GTFS | 800m | Count subway stations within radius. Weight each station by number of routes served. Cap contribution at 5+ stations. |
| **Development Trend** | 10% | DOB Permits | 400m | Count new-building and alteration permits filed in last 24 months. Higher activity = area trending up. |
| **Green Space** | 10% | NYC Parks | 500m | Distance to nearest park boundary. Bonus for parks >1 acre. |
| **Flood Risk** | 5% | PLUTO | Per-building | `firm07_flag` / `pfirm15_flag`. Binary — in flood zone or not. Score: 100 if no flag, 0 if flagged. |
| **Zoning & Density** | 5% | PLUTO | Per-building | Residential FAR, zoning district classification. Penalize heavy-commercial zones for residential livability. |

**Total: 100%**

Final score: weighted sum of component scores, each normalized to 0–100.

---

## Query Examples

### Crime within 400m of a point in Crown Heights

```
GET https://data.cityofnewyork.us/resource/5uac-w243.json
  ?$where=within_circle(geocoded_column, 40.6700, -73.9400, 400)
  &$select=law_cat_cd, ofns_desc, cmplnt_fr_dt
  &$limit=5000
  &$order=cmplnt_fr_dt DESC
```

### 311 noise complaints within 300m, last 12 months

```
GET https://data.cityofnewyork.us/resource/erm2-nwe9.json
  ?$where=within_circle(location, 40.6700, -73.9400, 300)
    AND created_date > '2025-02-26'
    AND complaint_type in ('Noise - Residential', 'Noise - Street/Sidewalk', 'Rodent')
  &$select=complaint_type, descriptor, created_date
  &$limit=5000
```

### PLUTO lookup for buildings near a point

```
GET https://data.cityofnewyork.us/resource/64uk-42ks.json
  ?$where=latitude between 40.6685 and 40.6715
    AND longitude between -73.9415 and -73.9385
  &$select=bbl, address, yearbuilt, numfloors, unitsres, firm07_flag, pfirm15_flag
```

### DOB violations for a specific building (join via BBL)

```
GET https://data.cityofnewyork.us/resource/3h2n-5cm9.json
  ?$where=boro='3' AND block='01234' AND lot='00001'
  &$select=violation_type, violation_category, issue_date, disposition_date
```

---

## Caching Strategy

Block-level data changes slowly. The recommended approach:

1. **Geohash bucketing** — Key scores by geohash at precision 7
   (~150m × 150m cells). Every listing maps to a geohash; if a score
   already exists for that cell and is <7 days old, reuse it.
2. **Batch pre-computation** — After each full listing sync, collect
   unique geohashes for all active listings and compute scores for any
   cells not yet cached (or stale). This avoids per-listing API calls
   at serve time.
3. **Incremental refresh** — Weekly cron re-scores all active
   geohashes. Crime and 311 data updates quarterly/daily, but
   block-level trends shift slowly.

Estimated API calls per full NYC score refresh (~5,000 unique
geohashes): ~35,000 calls across all sources. At 50,000/hour with an
app token, this completes in under an hour.

---

## Architecture Fit

The Block Quality Score lives in the **Intelligence layer** of the
three-part architecture:

```
┌─────────────┐     ┌──────────────────────┐     ┌───────────────┐
│  Ingestion   │────▶│  Intelligence + Serve │────▶│ Notifications │
│  (adapters)  │     │  (scoring, ranking,   │     │  (reactive    │
│              │     │   block quality, API)  │     │   alerts)     │
└─────────────┘     └──────────────────────┘     └───────────────┘
```

1. **Ingestion** pulls listings with lat/lon from source adapters.
2. **Intelligence** receives new/changed listings via the `changes`
   table, fetches block data for their geohash (if not cached),
   computes the composite score, and writes it to the listing record.
3. **Serve** exposes the pre-computed score alongside the listing.
4. **Notifications** uses score thresholds in user alert rules
   (e.g., "only notify me about listings with block score ≥ 70").

---

## Open Questions

- **Weight calibration**: The proposed weights are a starting point.
  Should be tunable per user preference (e.g., a user who doesn't
  drive may weight transit at 30% and flood at 0%).
- **Historic vs. current crime**: Current YTD (`5uac-w243`) captures
  recent trends; historic (`qgea-i56i`, ~9.5M records) captures
  longer baselines. Use a blended score?
- **School quality**: Not yet included. NYC DOE school zone boundaries
  and ratings could add a component for family-oriented renters.
- **Walk Score / bike infrastructure**: Not available in Open Data.
  Could integrate the Walk Score API (freemium) as a future source.
