# Architecture

> **Status, October 2026.** This is the design sketch from the first day of
> the project (26 February 2026). The split it describes still holds:
> ingestion writes canonical rows, scoring computes user-agnostic signals one
> dimension at a time, and serving combines them with per-user weights. What
> changed or was never built:
>
> - Survival probability and notifications were not built.
> - Ingestion is a command (`ingest.py`), not a scheduled job. `sync()` tracks
>   `first_seen_at` / `last_seen_at` and marks listings a source stops
>   reporting as inactive; price history is whatever the source supplies.
> - There are 17 scored dimensions rather than the deal score and five block
>   signals listed below, and flood risk became a flag.
> - The composite is not a dot product of raw signals. Dimensions are
>   averaged within five groups and the groups are averaged with equal
>   weights; a user can boost two groups and ignore dimensions. The value
>   shown is the listing's percentile among active listings. The default
>   feed sorts on a composite precomputed by `scripts/compute_composites.py`;
>   requests with custom weights compute and sort per request.
>
> The README describes the system as built.

## System Boundaries

The system is composed of two independent parts and a thin notification layer.

### Part 1 — Ingestion

Runs on a schedule (cron). No user-facing component.

Responsibilities:
- Pull from adapters (one per listing source)
- Normalize to canonical schema
- Upsert into SQLite
- Track `first_seen_at`, `last_seen_at`, price history

Output: a well-maintained DB. Nothing else.

### Part 2 — Intelligence + Serving

Reads from the DB. Runs on-demand or on its own schedule.

Responsibilities:
- Compute raw signals (deal score, block quality components, survival probability)
- Rank and filter to "top N worth acting on"
- Serve the dashboard

The serving layer does not care how data got into the DB — only that it's there and canonical.

---

## Processing Pipeline

```
Ingestion (cron)
    → writes raw canonical data to DB

Scoring (runs after ingestion)
    → reads DB, computes raw signals, writes scores back to DB

Consumers (both read from scored DB):
    ├── Dashboard (on-demand, serving)
    └── Alerts (push/SMS, event-driven on new high scores)
```

Ingestion stays pure. Scoring is the shared dependency — both
the dashboard and alerts consume it. Alerts are just another
consumer of scores, same as the dashboard.

---

## Scoring Model

### Scores Are User-Agnostic

All raw signals are absolute and computed once for all users.
A listing that is 15% below neighborhood median is underpriced
regardless of who is looking. Survival probability is a function
of price band and season. Block quality is block quality.

This is rental intelligence, not a recommendation engine.

### Raw Signals, Not Composite Scores

The scoring layer does not produce a single monolithic score.
It computes individual signals that are stored independently:

**Deal signals:**
- Underpriced delta ($)
- Deal score (0-100)

**Block quality signals:**
- Crime density
- Noise / 311 complaints
- Transit proximity
- DOB violations
- Flood risk

**Survival signals:**
- Probability listing disappears in 24h
- Probability listing disappears in 72h

### User-Specific Weighting

Users do not get different scores. They get different **weights**
on the same scores. The composite is computed at query time as
a dot product — cheap and per-request.

Examples:
- A deaf user sets noise weight to 0.
- A remote worker sets transit weight to 0.
- A light sleeper cranks noise weight up.
- A user who doesn't care about price shifts weight from deal
  score to block quality.

### User Preferences Control Two Things

1. **Filters** — what you see (beds, price, pets, neighborhood)
2. **Weights** — how raw signals combine into your composite ranking

Neither requires re-running the scoring pipeline per user.

---

## Notifications

Notifications are a thin consumer layer, not a separate system.

**Trigger:** After scoring runs, check newly scored listings
against each user's filters and weight thresholds.

**Flow:**
1. Ingestion writes to DB
2. Scoring computes raw signals
3. Notification process runs user filters against new high-score listings
4. Matches fire alerts (SMS, push, email)

Notifications never bypass scoring. They are downstream of it.
