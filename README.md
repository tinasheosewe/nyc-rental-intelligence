# NYC Rental Intelligence

Ranks New York City apartment listings by two questions: is the rent a good
deal against comparable listings, and what is it like to live at that
address. Each listing gets 17 scores: a deal score, an amenities score, and
15 block- and building-level scores (crime, street noise, road noise, pests,
bedbugs, building violations, landlord record, transit, parks, greenery and
more) computed from NYC Open Data, MTA data and OpenStreetMap. A FastAPI
service serves the ranked listings with a one-sentence explanation per
score, and a Next.js app shows them as a list, a map with citywide heatmaps,
and a compare view.

**The listings in this repository are synthetic.** A seeded generator
produces 300 fictional apartments, placed in real neighborhoods so the
scorers run on real city data. Listing ingestion is a small plug-in
interface; adapters for real listing sources are not included. The scorers
were tuned on real listing data, which is not included either; measurements
quoted in code comments and commit messages refer to that data.

## What is in here

- **19 scorers behind one interface** (`apthunt/scoring/`). Each computes one
  signal and writes a 0–100 score plus the components behind it. 17 feed the
  composite; flood zone and rent stabilization are flags. Every scorer's
  module docstring states its formula, radii and weights.
- **Absolute scores from frozen citywide baselines**
  (`apthunt/scoring/baseline.py`, `scripts/build_baseline.py`). A raw metric
  is mapped to its percentile in a frozen citywide distribution: the scorer
  is run over a sample of residential blocks from the PLUTO tax-lot file and
  a 1001-point quantile grid is stored. A single listing then scores the
  same as it would in a batch of thousands; before, scores were ranks within
  whatever batch was being scored. Transit and air quality are deliberately
  absolute instead (meters of walk, pollutant levels).
- **Corrections for what complaint data actually measures.** Area counts
  become per-household rates with a kernel-weighted PLUTO unit count as the
  denominator, so dense blocks are not scored as dangerous or loud for being
  dense. Incidents are weighted by distance (Gaussian kernels) and recency
  (180-day half-life), complaint volume is normalized by month, and repeat
  complaints from one location are capped. Building-level rates are shrunk
  toward the citywide rate (empirical Bayes), so a clean record on 3 units is
  weaker evidence than a clean record on 300.
- **A local data layer** (`apthunt/data/data_store.py`). 48 dataset
  definitions (46 Socrata, 2 OpenStreetMap via Overpass) are downloaded into
  SQLite with typed geo columns and swapped in atomically. Two NYPD feeds are
  merged into one derived table. Major roads are sampled into points every
  50 m, so road queries are ordinary radius queries.
- **An optional in-memory fast path** (`apthunt/data/spatial_index.py`):
  KD-trees behind the same `query_circle` API, hash maps for per-building
  lookups, and 10 m rasters for sightline and road-crossing checks. The
  SQLite path stays as the reference, and `scripts/bench_fast_path.py`
  compares the two.
- **Validation scripts** (`scripts/validate_scores.py`,
  `scripts/audit_cache_drift.py`). The first checks that the dimensions
  spread across the 0–100 range and that 13 pairs of known-contrast
  locations come out in the right order (a quiet Williamsburg side street
  must beat the nightlife core half a kilometer away on noise). The second
  recomputes a sample of stored scores from scratch to catch a formula that
  changed while its cache key did not.
- **Map layers produced by the scorers themselves**
  (`scripts/generate_heatmap_scores.py`): the citywide grids in
  `frontend/public/heatmap/` come from the same code path as listing scores.
- **API and UI** (`api/`, `frontend/`): composite with group weights and
  dealbreaker caps, explanations, flags, comparables, neighborhood peer
  context and a per-building record lookup; a Next.js client with list, map,
  detail, compare and shortlist views.

## Quick start

Needs Python 3.9+ and Node 20.9+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
(cd frontend && npm install)

./start.sh          # API on :8000, web app on :3000
```

`start.sh` first kills whatever is listening on ports 8000 and 3000, then
starts both dev servers; Ctrl-C stops them. It activates `.venv` if one
exists. If there is no database yet it builds a demo one
(`scripts/bootstrap_sample.sh`). The same steps by hand:

```bash
python3 ingest.py                                                          # 300 synthetic listings -> apthunt.db
python3 run_scores.py --no-fast --only deal,unit_amenities                 # no downloads
python3 run_scores.py --no-fast --only transit,schools,parks,convenience   # small NYC Open Data / MTA / OSM downloads
python3 scripts/compute_composites.py                                      # persist the default composite
```

Then open <http://localhost:3000>. The API has interactive docs at
<http://localhost:8000/docs>.

`requirements.txt` is grouped. The API, the scorers and the tests need only
the first group; numpy and scipy (the fast path) and the Streamlit group
(`dashboard.py`) are optional.

What to expect from the demo database:

- Six of the 17 dimensions are scored. Listings are marked "Limited data"
  and the unscored dimensions are simply absent. Without network access the
  open-data step is skipped, too few dimensions are scored, and the feed's
  default Data Availability filter hides every listing until it is set to
  "Any".
- No citywide baseline exists yet, so each scorer uses its documented
  fallback (a batch z-score for deal, for example). Composites are
  percentiles among the sample listings.
- Each sample listing sits at a random point in its neighborhood. Block
  scores are real for that spot. Building-level scores and facts describe
  whichever real tax lot is nearest, not the fictional apartment.
- Sample listings have no photos, link or broker.
- The demo is scored with `--no-fast`, straight from SQLite. The in-memory
  fast path (on by default, needs numpy and scipy) is built for runs over
  the full dataset; on a database that lacks the tables it preloads it logs
  that those parts stay on the SQLite path and carries on.

Tests run offline:

```bash
python3 -m unittest discover -s tests -t .
```

### Continuous integration

`.github/workflows/ci.yml` runs on pushes to `main`, on pull requests and on
demand. It uses no secrets and downloads no open data. Two jobs:

- **Python tests and API smoke test** (Python 3.12). Installs
  `requirements.txt` and runs the unit tests. Then it builds a database from
  the sample listings (`ingest.py`, the `deal` and `unit_amenities` scorers,
  `scripts/compute_composites.py`), starts the API, and requests
  `/api/health`, `/api/listings` and the detail of the first listing
  returned. Any response other than 200, or an empty feed, fails the job.
- **Web app lint and build** (Node 22). `npm ci`, `npm run lint` and
  `npm run build` in `frontend/`. An ESLint error or a failed build, which
  includes the TypeScript check, fails the job. ESLint still prints
  `@next/next/no-img-element` warnings: listing photos are plain `<img>` tags
  because their URLs point at arbitrary remote hosts.

## Scoring every dimension

The remaining scorers download what they need on first use, and several of
those tables are large. With the filters in the dataset registry, the 46
Socrata datasets held about 12.6 million rows when counted in October 2026.
The biggest are HPD violations and DOB violations (about 1.8 million rows
each), 311 noise, rodent and heat complaints (1.2 million), HPD complaints
(1.0 million), NYPD complaints (1.0 million across two feeds) and PLUTO
(0.86 million). Expect a long first run and a large database.

```bash
export SODA_APP_TOKEN=...               # optional; anonymous Socrata requests are throttled
python3 manage_data.py                  # download every dataset (--only a,b for some, --status for freshness)
python3 run_scores.py                   # every scorer (--only to pick, --list to see them)
python3 scripts/build_baseline.py       # freeze citywide baselines from 8,000 residential cells
python3 scripts/build_baseline.py --from-listings --only deal,unit_amenities,building_violations,management,pest,bedbug
python3 run_scores.py                   # rescore against the baselines
python3 scripts/compute_composites.py
python3 scripts/validate_scores.py      # spread gates and ground-truth pairs
```

Deal and unit amenities can only be baselined from listings. The four
building-level dimensions are baselined against buildings that have listings
rather than all residential blocks, for the reason given in
`scripts/build_baseline.py`. That mode needs at least 100 scored listings
per dimension. Building-level rates use the baseline median as their prior,
so they settle after a second baseline-and-rescore cycle.

Other scripts, each with its usage in the docstring:

| Script | Purpose |
| --- | --- |
| `scripts/audit_cache_drift.py` | Recompute sampled listings from scratch and compare with stored components |
| `scripts/bench_fast_path.py` | Parity check and timings, fast path against SQLite (needs numpy and scipy) |
| `scripts/generate_heatmap_scores.py` | Regenerate `frontend/public/heatmap/*.json` |
| `scripts/repair_park_geometry.py` | Replace degenerate park outlines in the Parks feed with OpenStreetMap geometry |
| `scripts/repair_geocodes.py` | Correct listing coordinates that disagree with the PLUTO address |
| `scripts/migrate_geo_types.py` | One-time migration for databases created before geo columns were typed |

## The dimensions

Groups are the unit of weighting in the composite. "Baseline" means the
score is a percentile of the frozen citywide (or listing-population)
distribution once one has been built.

| Dimension | Group | What is measured | Scale |
| --- | --- | --- | --- |
| `deal` | value | Rent against comparable listings (same neighborhood and bed count): price, $/sqft, size, tenant tenure estimated from relist history, fresh price cuts | baseline |
| `unit_amenities` | value | Weighted count of in-unit and building amenities | baseline |
| `transit` | access | Walk to the nearest subway entrance, lines within 800 m (lines that reach the Manhattan core count more), bus routes, penalty for crossing major roads | absolute |
| `crime` | safety | NYPD complaints within 400 m by severity, offense and premise type, plus shootings, per 1,000 households | baseline |
| `street_danger` | safety | Pedestrian and cyclist crash injuries within 300 m, weighted by whether the crash is on the listing's own street or on an arterial it only crosses, per 1,000 households | baseline |
| `shelter` | safety | 311 encampment complaints within 200 m; homeless-services facilities and NYCHA developments within 800 m | baseline |
| `noise` | neighborhood | 311 noise complaints within 150 m per 1,000 households, with a liquor-license density prior | baseline |
| `road_exposure` | neighborhood | Highway distance (reduced when rows of buildings stand in between), arterial density, truck routes, elevated trains, bus corridors, firehouses | baseline |
| `air_quality` | neighborhood | PM2.5 and NO2 for the community district, adjusted for nearby major roads | absolute |
| `parks` | neighborhood | Best nearby park by distance, size tier and type | baseline |
| `greenery` | neighborhood | Street trees, canopy and community gardens | baseline |
| `convenience` | neighborhood | Groceries and supermarkets by size, pharmacies, gyms, laundromats and restaurants within 500 m | baseline |
| `schools` | neighborhood | Best public high school within about 1.5 km by attendance and student safety; counted only in "kids mode" | baseline |
| `building_violations` | building | Open DOB and HPD violations weighted by type, class and age, per unit | baseline |
| `management` | building | HPD complaints across the owner's portfolio per unit, own-building heat complaints, HPD litigation, evictions | baseline |
| `pest` | building | Rodent inspection failures and 311 rodent complaints within 100 m, plus the building's HPD pest complaints | baseline |
| `bedbug` | building | Annual bedbug filings per unit, re-infestations weighted double, adjacent buildings included | baseline |
| `flood_risk` | flag | FEMA flood-zone flags on the nearest tax lot | flag |
| `rent_stabilized` | flag | Built before 1974 with six or more units (a building-level heuristic) | flag |

The composite (`api/composite.py`) averages scored dimensions within each
group and averages the groups with equal weights. A user can boost up to two
groups and ignore individual dimensions. Missing dimensions are left out
rather than counted as zero. The result is shown as its percentile among
active listings, and a bottom-tier score on bedbugs, violations, management,
pests or crime caps it at 55.

These scores are built from complaint, inspection and enforcement records,
which reflect who reports and who gets inspected as well as actual
conditions. The corrections above reduce that; they do not remove it.

## Listing ingestion

A listing source is anything that yields listings in the canonical shape.
The contract is in `apthunt/ingest/__init__.py`:

- Subclass `ListingSource`, give it a `name`, and yield one dict per
  currently available listing from `fetch()`. `source_id`, `price`, `beds`,
  `lat` and `lon` are required; any key outside the canonical field list is
  an error.
- Register it with `register_source` and run
  `python3 ingest.py --source <name>`.
- The source never touches the database. `sync()` owns the schema, upserts
  on `(source, source_id)`, sets `first_seen_at` on first sight and
  `last_seen_at` on every sight, marks listings the source no longer reports
  as inactive, and writes one `sync_log` row per run. A listing that fails
  validation fails the whole run.

The one source included is `sample` (`apthunt/ingest/sample.py`):
deterministic for a given seed and date, with invented street names, rents,
concessions and price histories.

Canonical fields:

| Group | Fields |
| --- | --- |
| Provenance | `source_id`, `url` |
| Location | `address`, `unit`, `neighborhood`, `borough`, `zip`, `lat`, `lon` |
| Cost | `price`, `net_effective_price`, `no_fee`, `months_free`, `lease_term_months` |
| Unit | `beds`, `baths`, `sqft`, `amenities`, `pets_allowed`, `furnished`, `description` |
| Media, availability | `photos`, `available_at` |
| Contact | `broker_name`, `broker_firm`, `broker_phone`, `broker_email` |
| History | `price_history` (list of `{date, price, event}`), `relist_count` |
| Building | `building_year`, `building_stories` |
| Raw payload | `raw_json`; the deal scorer reads `price_delta`, `price_changed_at` and `months_free` from it when present |
| Managed by `sync()` | `id`, `source`, `first_seen_at`, `last_seen_at`, `status` |

## API

`uvicorn api.app:app` serves:

| Endpoint | Returns |
| --- | --- |
| `GET /api/listings` | Paginated feed. Filters: beds, price range, neighborhoods, rent stabilized, minimum composite, minimum sqft, available before, amenities, data availability. Sort: composite, price, a group or a dimension. Weights: `priorities`, `ignore`, `kids_mode` |
| `GET /api/listings/{id}` | One listing with score components, a one-sentence explanation per dimension, flags, transit stations, nearby places, similar and "also consider" listings, neighborhood peer context and building records |
| `GET /api/neighborhoods`, `GET /api/amenities` | Values for the filter controls |
| `GET /api/health` | Database summary; 503 when there are no active listings with coordinates |

Neighborhood peer context appears only where at least 20 listings in the
neighborhood have a score for the dimension, which the default 300-listing
sample does not reach. Building records need the HPD, DOB and DOF datasets.

Environment variables:

| Variable | Used by | Meaning |
| --- | --- | --- |
| `APTHUNT_DB_PATH` | everything | SQLite file (default `apthunt.db` in the repository root) |
| `APTHUNT_REQUIRE_LISTINGS` | API | `false` lets the API start on a database with no visible listings |
| `CORS_ORIGINS` | API | Extra allowed origins, comma-separated (`http://localhost:3000` is always allowed) |
| `SODA_APP_TOKEN` | data downloads | Socrata app token |
| `NEXT_PUBLIC_API_URL` | web app | Backend that `/api/*` is proxied to (default `http://localhost:8000`) |
| `APTHUNT_DB_URL` | `build.sh` | Pre-built database to download instead of building the demo one |

## Web app and dashboard

`frontend/` is the main client; see `frontend/README.md`. Its interface
carries the project's working brand, RESIDE; the Python package and API are
named `apthunt`.

`dashboard.py` is a Streamlit admin view over the same database (map, score
distributions, neighborhood table, listing detail):

```bash
streamlit run dashboard.py
```

## Repository layout

```
apthunt/ingest/      listing source interface, canonical schema, sync, sample generator
apthunt/scoring/     Scorer interface, engine, 19 scorers, baselines, shared statistics
apthunt/data/        dataset registry and downloads, block cache, in-memory indexes, GTFS loader
api/                 FastAPI app: feed, detail, composite, flags, explanations, comparables
frontend/            Next.js web app; public/heatmap/ holds the precomputed score grids
scripts/             baselines, composites, validation, heatmaps, data repairs, demo bootstrap
tests/               offline tests: generator, sync semantics, offline scorers, API
data/                MTA subway GTFS files (see below)
ingest.py  run_scores.py  manage_data.py     command-line entry points
dashboard.py         Streamlit admin dashboard
start.sh             local dev servers
build.sh  render.yaml                        Render deployment
design.html  color.html                      static look-and-feel mockups (dark and light palette) with placeholder content
ARCHITECTURE.md  BLOCK_QUALITY_SCORE.md  UI_PLAN.md   early design documents, each with a note on what changed
.claude/launch.json  dev-server launch configuration for Claude Code
.github/workflows/ci.yml                     GitHub Actions workflow (see "Continuous integration")
```

## Data sources and licences

Downloaded at run time, not redistributed here:

- [NYC Open Data](https://opendata.cityofnewyork.us/): PLUTO, NYPD
  complaints, shootings and collisions, 311 service requests, DOB and HPD
  violations, complaints, litigation and registrations, marshal evictions,
  bedbug filings, rodent inspections, parks, street trees, community
  gardens, the Facilities Database, NYCHA buildings, truck routes, the
  Community Air Survey and others. The dataset ids are in
  `apthunt/data/data_store.py`.
- [New York State Open Data](https://data.ny.gov/): MTA subway entrances,
  stations and bus stops, retail food stores, liquor licences.
- [OpenStreetMap](https://www.openstreetmap.org/copyright) through the
  Overpass API: shops and amenities, major roads, and park outlines used to
  repair the Parks feed. © OpenStreetMap contributors, available under the
  Open Database License.

Included in the repository:

- `data/stops.txt`, `routes.txt`, `trips.txt` and `stop_times.txt` are four
  files of the MTA's static subway GTFS feed, added in February 2026 and
  included as published. They were obtained from the MTA and are
  redistributed under the
  [MTA data feed terms](https://www.mta.info/developers/terms-and-conditions).
  This project is not affiliated with or endorsed by the MTA. The files give
  each station its routes for the detail view and let the transit scorer run
  without a download. `stop_times.txt` is 36 MB.
- `frontend/public/heatmap/*.json` are 120 × 123 grids of scores for crime,
  noise, pests, transit, green space and convenience, generated in July 2026
  by `scripts/generate_heatmap_scores.py` from the sources above. They are
  statistics derived from those datasets. The transit, green-space and
  convenience layers include OpenStreetMap-derived inputs, so OpenStreetMap's
  attribution and Open Database License terms apply to them.
- The web map uses CARTO basemap tiles (© OpenStreetMap contributors,
  © CARTO), loaded from CARTO at run time.
- `design.html` and `color.html` reference three Unsplash photos by URL.

## Deployment

`render.yaml` describes two Render services, the API and the web app.

- `build.sh` makes sure a database exists: it downloads one from
  `APTHUNT_DB_URL` when that is set, and otherwise builds the demo database
  from the sample listings.
- The build fails if the database is missing, is not valid SQLite, has no
  `listings` table or has no listings.
- The API refuses to start, and the health check returns `503`, when the
  database has no active listings with coordinates.

## Status

Built: the ingestion interface and sync, the 19 scorers, citywide baselines,
the validation and audit scripts, the API, the web app, heatmap generation,
the Streamlit dashboard, a Render blueprint.

Planned in the original design and not built:

- Cross-source deduplication and scheduled sync. `sync()` does not diff
  prices between runs or detect relists; price history and relist counts are
  whatever the source supplies.
- A survival model (how soon a listing will be gone).
- Alerts by SMS, push or email.
- Extracting amenities from description text; relist detection from photos.
- Commute-time scoring; broker enrichment.

## Background

The project started from one idea: listing sites optimize for browsing and
engagement, and a renter needs the opposite, which the first README called
decision compression: a short ranked list with the reasons attached. Two
design rules from that first sketch still shape the code. No single
source owns the data model: sources are adapters, and the canonical schema
is driven by what the scoring needs. And scores are the same for every
user; preferences change weights and filters, never the scores themselves.

`ARCHITECTURE.md`, `BLOCK_QUALITY_SCORE.md` and `UI_PLAN.md` are the design
documents from February 2026. Each opens with a note on what was built
differently.
