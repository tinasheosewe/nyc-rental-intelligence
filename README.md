# NYC Rental Intelligence

## Overview

NYC Rental Intelligence is a signal-first apartment hunt platform built
specifically for New York City.

Unlike traditional listing sites that optimize for browsing and
engagement, this system optimizes for **decision compression**. It
centralizes listings from any source, detects true freshness, ranks
underpriced units, scores micro-block quality, and only surfaces
apartments worth acting on.

The goal is simple: **Show the top 3 apartments that matter today.
Suppress everything else.**

## Production DB Seed

The Render backend expects a valid SQLite file at startup.

- `build.sh` downloads `apthunt.db` from `APTHUNT_DB_URL` when the file is missing.
- The build now fails if the URL returns a non-200 response, if the downloaded file is not valid SQLite, or if it does not contain the `listings` table.
- The API health check returns `503` when the database has no API-visible active listings.
- Optional overrides: `APTHUNT_DB_URL`, `APTHUNT_DB_PATH`, `APTHUNT_REQUIRE_LISTINGS`.

---

## Core Philosophy

- Inventory-first platforms create noise.
- This platform creates **signal**.
- Users should not scroll endlessly.
- Users should not wonder if a listing is new.
- Users should not manually evaluate every block.
- If nothing meets criteria, the system says so clearly.
- **No single source owns the data model.** Sources are adapters.
  The canonical schema is driven by what the intelligence layer needs
  to score, rank, and alert — not by what any upstream API happens
  to expose.

---

## Canonical Listing Schema

The schema is defined by two questions:

1. **What does the scoring engine need?** Every field must feed at
   least one of: deal scoring, freshness tracking, micro-block
   intelligence, survival modeling, or user preference filtering.
2. **Can any source reasonably provide it?** A field belongs in the
   schema if at least one realistic source can populate it. Fields
   that no source can fill are omitted, not left perpetually null.

```json
{
  "id": "uuid",
  "source": "<adapter name> | manual",
  "source_id": "1234567",
  "url": "https://...",

  "address": "123 Example Avenue",
  "unit": "4A",
  "neighborhood": "Crown Heights",
  "borough": "Brooklyn",
  "zip": "11213",
  "lat": 40.6700,
  "lon": -73.9400,

  "price": 3000,
  "net_effective_price": 2750,
  "no_fee": false,
  "months_free": 1.5,
  "lease_term_months": 14,

  "beds": 1,
  "baths": 1.0,
  "sqft": 650,
  "amenities": ["washer_dryer", "dishwasher"],
  "pets_allowed": true,
  "furnished": false,
  "description": "Renovated 1BR with...",

  "photos": ["https://..."],
  "available_at": "2026-02-24",

  "broker_name": "Jane Smith",
  "broker_firm": "Example Realty",
  "broker_phone": "212-555-0100",
  "broker_email": "jane@example.com",

  "first_seen_at": "2026-02-24T00:00:00Z",
  "last_seen_at": "2026-02-26T18:00:00Z",
  "status": "active"
}
```

### Why each field exists

| Field               | Feeds                                          |
| ------------------- | ---------------------------------------------- |
| `price`, `net_effective_price`, `months_free` | Deal scoring, comp deviation |
| `no_fee`, `lease_term_months`                 | True cost calculation        |
| `beds`, `baths`, `sqft`, `amenities`          | Comp grouping, user filters  |
| `lat`, `lon`, `neighborhood`, `zip`           | Micro-block scoring, comps   |
| `pets_allowed`, `furnished`                   | User preference filters      |
| `photos`                                      | Relist detection (hash diff) |
| `description`                                 | NLP extraction, amenity fill |
| `available_at`                                | Urgency / survival model     |
| `broker_name`, `broker_firm`, `broker_phone`, `broker_email` | Actionability — user needs to contact someone |
| `first_seen_at`, `last_seen_at`               | True freshness engine        |
| `source`, `source_id`, `url`                  | Dedup, provenance, linking   |

### What is intentionally excluded

- **Building metadata** (year built, unit count, building type).
  Useful for enrichment but does not drive any scoring model.
  Can live in a separate `buildings` table if needed later.
- **Media counts, 3D tour flags, video flags.** Cosmetic. Don't
  affect deal score, freshness, or survival probability.
- **Source-specific taxonomy** (area codes, building type
  enums, source type labels). These are adapter-internal concerns,
  not canonical fields.
- **Nearby transit.** Derived from geo coordinates + MTA GTFS data.
  Not a listing attribute — it's a computed micro-block metric.

---

## Source Adapter Model

Each listing source is an adapter behind a common interface:

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Source A    │     │  Source B    │     │  Listings    │
│   Adapter    │     │   Adapter    │     │  Project     │
│              │     │              │     │  Adapter     │
│  API →       │     │  Feed →      │     │  Email →     │
│  canonical   │     │  canonical   │     │  canonical   │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       └────────────────────┼────────────────────┘
                            ▼
                   ┌────────────────┐
                   │  Canonical DB  │
                   │  (unified)     │
                   └────────┬───────┘
                            ▼
                   ┌────────────────┐
                   │  Intelligence  │
                   │  Pipeline      │
                   └────────────────┘
```

**Adapter contract:** Each adapter must:

1. **Paginate** through its source's full active inventory.
2. **Map** source fields → canonical schema. Unmappable fields are
   dropped, not shoehorned into the schema.
3. **Yield** canonical listing dicts. The adapter does not touch the
   DB — the sync orchestrator handles upsert, dedup, and freshness.
4. **Report** sync metadata (total count, pages pulled, errors).

### Per-adapter notes

| Adapter | Source | Broker data? | Unique value |
| ------- | ------ | ------------ | ------------ |
| **Listings Project** | Email digest / web | Usually included | Curated, personal landlord listings |
| **Manual / email** | User-submitted | User provides | Fills gaps from direct landlord postings |

---

## Enrichment Sources

These fill gaps that no listing adapter can cover.

| Source                   | Fills                         | Access              |
| ------------------------ | ----------------------------- | ------------------- |
| **NYC Open Data**        | Crime, 311, DOB violations    | Free API            |
| **MTA GTFS**             | Transit proximity + routes    | Free data           |
| **FEMA / NYC Planning**  | Flood zone overlays           | Free GIS data       |
| **REBNY / RLS feed**     | Agent name, phone, firm       | Licensed brokers    |
| **NYC ACRIS**            | Building ownership records    | Public data         |
| **User-submitted**       | Forwarded listing emails      | Email parsing       |

---

## Feature Set

### 1. Multi-Source Inventory Sync

- Adapter-based ingestion — each source is a plugin
- Hourly sync cadence per adapter
- SQLite storage with upsert (first_seen / last_seen tracking)
- Cross-source dedup (address + unit normalization)
- Exponential backoff on errors, per-adapter sync logging

### 2. True Freshness Engine

Track:
- First seen timestamp (our clock, not the source's)
- Last seen timestamp
- Price changes (diff between syncs)
- Relisting detection (listing disappears then reappears)
- Photo hash similarity (detect relists with new photos)

Display:
- True market age (days since first_seen_at)
- Price change history
- Relist flag

### 3. Micro-Block Intelligence

NYC Open Data + MTA GTFS integrations:
- Crime density (per census block)
- 311 complaint density
- NYCHA proximity
- DOB violations (active violations on building)
- Flood zone overlays
- Subway entrance proximity (computed from lat/lon + GTFS)

Output: **Block Quality Score (0–100)**

### 4. Deal Scoring

For each listing:
- Compare against micro-area comps (same neighborhood, same bed count)
- Compare against building comps when available
- Adjust for true cost (no-fee, months free, net effective)
- Adjust for seasonality

Output:
- Underpriced delta ($)
- **Deal Score (0–100)**

### 5. Survival Probability Model

Estimate:
- Probability listing disappears in 24h
- Probability listing disappears in 72h

Based on:
- Price band
- Neighborhood
- Season
- Historical time-on-market curves (built from our sync data)

### 6. Signal-First Dashboard

- Top ranked listings only
- No infinite scroll
- Clear explanation for each score
- "Nothing worth acting on today" state

### 7. Priority Alerts

- SMS alerts for high-score listings
- Push notifications
- Email fallback

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     INGESTION                            │
│                                                         │
│  Adapter interface (common contract)                    │
│    ├─ ListingsProjectAdapter → email/web     [planned]  │
│    └─ ManualAdapter        → user-submitted  [planned]  │
│                                                         │
│  Sync orchestrator (hourly cron)                        │
│    ├─ Calls each adapter's paginate + map               │
│    ├─ Upserts into canonical DB                         │
│    ├─ Handles dedup (address + unit + photo hash)       │
│    └─ Logs sync metadata per adapter                    │
├─────────────────────────────────────────────────────────┤
│                    PROCESSING                            │
│                                                         │
│  ├─ Address normalization + geocoding                   │
│  ├─ NLP extraction from descriptions                    │
│  ├─ Amenity classification                              │
│  ├─ Micro-block scoring (NYC Open Data + MTA)           │
│  ├─ Comp calculation + deal scoring                     │
│  └─ Survival probability estimation                     │
├─────────────────────────────────────────────────────────┤
│                     STORAGE                              │
│                                                         │
│  SQLite (single file, portable)                         │
│    ├─ listings          (canonical schema, all sources)  │
│    ├─ sync_log          (per-adapter audit trail)        │
│    ├─ price_history     (change tracking)                │
│    ├─ block_scores      (micro-block metrics)            │
│    └─ comps             (neighborhood medians)           │
├─────────────────────────────────────────────────────────┤
│                   PRESENTATION                           │
│                                                         │
│  ├─ Signal dashboard (top N only)                       │
│  ├─ SMS / push / email alerts                           │
│  └─ "Nothing today" zero-state                          │
└─────────────────────────────────────────────────────────┘
```

---

## Development Phases

### Phase 1 — Foundation (current)

- [ ] Define adapter interface (abstract base class)
- [ ] Canonical DB schema (source-agnostic)
- [ ] Run first full sync, validate data
- [ ] Price change tracking (diff between syncs)
- [ ] Basic deal scoring (vs. neighborhood median)
- [ ] Minimal CLI dashboard

### Phase 2 — Intelligence + Second Source

- [ ] Second source adapter (proves the adapter model works)
- [ ] Cross-source dedup engine
- [ ] Micro-block scoring (NYC Open Data integration)
- [ ] Survival probability model (from historical sync data)
- [ ] Relisting detection
- [ ] SMS alerts for high-score listings
- [ ] Broker enrichment pipeline

### Phase 3 — Product

- [ ] Web dashboard
- [ ] Listings Project adapter
- [ ] Manual / email submission adapter
- [ ] Commute-time scoring overlay
- [ ] Negotiation leverage signals (days on market, price drops)
- [ ] Lifestyle scoring (nightlife, parks, grocery proximity)

---

## Monetization Model

**Primary:**
- 60-day Hunt Pass
- Premium positioning
- High-signal renter focus

**Future:**
- Concierge tier
- Commute optimization add-on
- Advanced negotiation insights

---

## Success Criteria

The product succeeds if:

- Users stop browsing multiple platforms.
- Users act faster on high-signal listings.
- Users save money or avoid poor micro-locations.
- Users confidently see when no good options exist.

**This is not a listing site. This is NYC rental intelligence.**
