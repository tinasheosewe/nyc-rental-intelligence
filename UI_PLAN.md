# AptHunt — UI Design Plan

> **Status, October 2026.** This is the UI plan written before the frontend
> existed (28 February 2026). Most of its surfaces were built: feed and scan
> views, watchlist and shortlist, compare with table, flags and radar tabs, a
> map with overlays, the filter sheet, settings and the shortlist tray. The
> result differs from the plan in these ways:
>
> - Layout: on desktop the app is three panels (list, map, detail) with the
>   map always visible. The full-width feed / scan canvas and the top bar
>   remain on small screens only.
> - Dimensions: 17 in five groups (value, access, neighborhood, safety,
>   building), not 11. The sort chips are Composite plus the five groups, and
>   compare works on groups.
> - Weights: the drag-to-rank priority list was replaced by "boost up to two
>   groups" and "ignore dimensions". Saved configs and accounts were not
>   built.
> - Scores are shown as tier labels ("Very Safe", "Quiet") with the numbers
>   in the breakdowns.
> - Theme: light cream and amber only, with sky / violet / orange / red score
>   colors. There is no dark mode.
> - Map: Leaflet on a CARTO basemap. The overlays are precomputed citywide
>   grids for crime, noise, transit, green space, convenience and pests;
>   there is no flood layer and there are no amenity markers.
> - Not built: drag interactions (reordering, drag-to-compare, long-press)
>   and batch actions other than compare. Of the listed transitions, the feed
>   slide and the sheet and modal transitions exist; the card-to-grid morph,
>   cards flying into the compare table and the tray animations do not.
> - The response shape under "Data Requirements" is an early sketch;
>   `api/models.py` is the current one. Deployment is a Render blueprint
>   (`render.yaml`), not Vercel plus Railway or Fly.io.
>
> `frontend/README.md` describes the app as built.

## Design Philosophy

**Sleek, modern, simple, elegant.** AptHunt is not a search engine — it's a scorecard feed. We pre-score every listing across 11 dimensions and present them ranked. Users triage, curate, and compare with depth — not volume.

**Core principle**: Show fewer listings with more insight, not more listings with less.

---

## Architecture Overview

**No left panel.** Full-width canvas with a top bar, sort chips, and a persistent bottom tray. All configuration (filters, preferences) lives behind on-demand sheets/modals — not permanent screen real estate.

```
┌──────────────────────────────────────────────────┐
│  Top Bar                                         │
├──────────────────────────────────────────────────┤
│  Sort Chips                                      │
├──────────────────────────────────────────────────┤
│                                                  │
│                                                  │
│               Full-Width Canvas                  │
│          (changes based on active view)          │
│                                                  │
│                                                  │
├──────────────────────────────────────────────────┤
│  Pinned Shortlist Tray                           │
└──────────────────────────────────────────────────┘
```

---

## Top Bar (~56px)

Always visible. Contains:

| Position | Element | Behavior |
|----------|---------|----------|
| Left | Logo / "AptHunt" | Home / brand |
| Center | Queue tabs as pills: `Explore` · `Watchlist (n)` · `Shortlist (n)` | Switches canvas view. Badge shows item count. |
| Right | Icons: 🔍 Filter · ⚙ Settings · 🗺 Map · ▤/▦ View toggle | Filter opens sheet. Settings opens preferences. Map toggles overlay. View toggle switches Feed ↔ Scan (Explore only). |

---

## Sort Chips (~40px, below top bar)

Horizontal scrollable row of pill buttons. One active at a time.

**Chips**: `Composite` · `Deal` · `Transit` · `Crime` · `Noise` · `Flood Risk` · `Violations` · `Parks` · `Schools` · `Amenities` · `Management`

- **Composite** is selected by default (influenced by user's priority ranking)
- Tapping a chip re-sorts the active queue by that score dimension, descending
- Active chip is visually highlighted (filled vs. outlined)
- `Rent Stabilized` is a filter toggle, not a sort — it lives in the Filter sheet

---

## Surfaces

### 1. Explore — Feed Mode (Default)

**Layout**: Single full-viewport card. One listing fills the canvas. Horizontal swipe (or arrow keys on desktop) moves between listings.

**Purpose**: Deep discovery. Users evaluate each listing with full scoring context before deciding.

#### Above the Fold (no scroll needed)

```
┌──────────────────────────────────────┐
│                                      │
│     📸 Hero Photo (full-width)       │
│                                      │
├──────────────────────────────────────┤
│                                      │
│  $2,400/mo · 1BR · East Village     │
│  142 E 3rd St, Apt 4B               │
│                                      │
│         ┌───────────┐               │
│         │    87     │  Composite    │
│         │   ●●●●○  │               │
│         └───────────┘               │
│                                      │
│  Top-3 score pills (user's priorities):
│  [Transit 91] [Crime 88 ↓] [Deal 72]│
│                                      │
│  ┌──────────┐      ┌──────────┐     │
│  │   Skip   │      │   Save   │     │
│  └──────────┘      └──────────┘     │
│                         ★ Shortlist │
└──────────────────────────────────────┘
```

- **Composite score**: Large, prominent, color-coded (green/yellow/orange/red)
- **Top-3 pills**: The user's top 3 priority dimensions shown as mini badges
- **Skip**: Removes from Explore (not permanent — can resurface via filters)
- **Save**: Adds to Watchlist
- **★ Shortlist**: Small icon/link — adds directly to Shortlist for instant favorites

#### Below the Fold (scroll to reveal)

```
│  ── Score Breakdown ──────────────── │
│  Transit     ████████████░░░░ 91    │
│  Deal        █████████░░░░░░░ 72    │
│  Crime       ████████████░░░░ 88 ↓  │
│  Noise       ██████░░░░░░░░░░ 45 ↑  │
│  Amenities   █████████░░░░░░░ 78    │
│  Parks       ███████████░░░░░ 84    │
│  Schools     ██████████░░░░░░ 80    │
│  Flood Risk  ████████████████ 95    │
│  Violations  ███████░░░░░░░░░ 55    │
│  Management  █████████░░░░░░░ 71    │
│  Rent Stab.  ✓ Yes                  │
│                                      │
│  ── Insights (Flags) ───────────── │
│  🟢 Crime down 12% over 6 months   │
│  🟢 Rent-stabilized unit           │
│  🟢 4 subway lines within 0.3 mi   │
│  🔴 3 open DOB violations          │
│  🟡 Noise trend slightly rising    │
│  🔴 Management: 14 HPD complaints  │
│                                      │
│  ── Map Snippet ─────────────────── │
│  ┌────────────────────────────────┐ │
│  │  [mini map with pin,          │ │
│  │   nearby transit, parks]      │ │
│  └────────────────────────────────┘ │
│                                      │
│  ── Building Details ────────────── │
│  Owner: XYZ Management LLC          │
│  Year Built: 1925                   │
│  Units: 24                          │
│  DOB Violations: 3 open / 12 total  │
│  HPD Complaints (12mo): 14          │
└──────────────────────────────────────┘
```

- **Score Breakdown**: All 11 scores as horizontal bars with numeric values. Trend arrows (↑↓) on crime and noise.
- **Insights**: Auto-generated green/red/yellow flag sentences. The "why should I care" summary.
- **Map Snippet**: Small inline map showing the listing pin with nearby transit stops, parks, schools.
- **Building Details**: Raw data backing the management and violations scores.

#### Navigation

- **Horizontal swipe** (or ← → arrow keys): Next/previous listing
- Users can freely navigate back and forth — no Hinge-style one-way constraint
- Position is preserved when switching tabs and returning

---

### 2. Explore — Scan Mode

**Toggle**: View icon in top bar (▤ ↔ ▦) switches between Feed and Scan.

**Layout**: Compact card grid, 3 columns on desktop, 2 on mobile.

```
┌────────┐  ┌────────┐  ┌────────┐
│ 📸     │  │ 📸     │  │ 📸     │
│ $2,400 │  │ $2,100 │  │ $2,800 │
│ 1BR EV │  │ 2BR WH │  │ 1BR LES│
│  87 🟢 │  │  79 🟢 │  │  92 🟢 │
└────────┘  └────────┘  └────────┘
┌────────┐  ┌────────┐  ┌────────┐
│ 📸     │  │ 📸     │  │ 📸     │
│ $1,900 │  │ $3,200 │  │ $2,600 │
│ STU UWS│  │ 2BR BK │  │ 1BR CH │
│  64 🟡 │  │  91 🟢 │  │  58 🟠 │
└────────┘  └────────┘  └────────┘
```

Each card shows: thumbnail, price, bed count + neighborhood abbreviation, composite score with color.

**Tap any card** → jumps into Feed mode at that listing's position.

**Purpose**: Quick overview when 200+ listings match. Skim scores, spot interesting ones, tap to dive in.

---

### 3. Watchlist (Grid)

**Layout**: Same compact grid as Scan mode, but with **checkboxes** on each card.

```
┌────────┐  ┌────────┐  ┌────────┐
│ 📸     │  │ 📸     │  │ 📸     │
│ $2,400 │  │ $2,100 │  │ $2,800 │
│ 1BR EV │  │ 2BR WH │  │ 1BR LES│
│  87 🟢 │  │  79 🟢 │  │  92 🟢 │
│ ☐      │  │ ☑      │  │ ☑      │
└────────┘  └────────┘  └────────┘
```

**Interactions**:
- **Tap card** → opens full-viewport card view (same as Feed mode card, but with "Move to Shortlist" and "Remove" actions instead of Skip/Save)
- **Checkbox** → multi-select for batch actions
- **Batch toolbar** (appears when ≥1 selected): `Compare (n)` · `Move to Shortlist` · `Remove`
- **Drag handle** on each card for manual reordering (override score-based sort)
- **Empty state**: Illustration + *"Swipe right on listings in Explore to add them here"*

**Purpose**: Light bookmarking. "Maybe" candidates. A parking lot for interesting listings before you commit them to Shortlist.

---

### 4. Shortlist (Grid)

**Layout**: Identical to Watchlist grid. Same checkboxes, same batch toolbar, same tap-to-expand.

**Differences**:
- "Move to Watchlist" (demote) replaces "Move to Shortlist"
- These are the serious contenders
- Pinned tray at bottom mirrors Shortlist contents

**Purpose**: Your top picks. The ones you'd actually schedule viewings for. Source for structured comparison.

---

### 5. Compare Mode

**Entry points**:
- Select 2–5 listings via checkboxes in Watchlist or Shortlist → tap "Compare"
- Drag two pinned-tray avatars together (delightful micro-interaction)

**Step 1 — Metric Picker** (modal):
- Choose 4–6 score dimensions to compare (checkboxes from 11 available)
- User's top-3 priorities are pre-checked
- Optional: set weight per metric (Low / Medium / High) — defaults to equal

**Step 2 — Comparison Canvas** with three tab modes:

#### Tab A: Table View

```
│              │ 142 E 3rd │ 89 Ave A  │ 312 W 4th │
│              │    📸      │    📸      │    📸      │
├──────────────┼───────────┼───────────┼───────────┤
│ Transit      │  91 🥇    │  84 🥈    │  78 🥉    │
│ Crime        │  88 🥇    │  72       │  85 🥈    │
│ Deal         │  72       │  89 🥇    │  65       │
│ Amenities    │  78 🥈    │  81 🥇    │  45       │
│ Noise        │  45       │  62 🥈    │  71 🥇    │
├──────────────┼───────────┼───────────┼───────────┤
│ COMPOSITE    │  76 🥈    │  79 🥇    │  69       │
│              │           │ ★ WINNER  │           │
└──────────────┴───────────┴───────────┴───────────┘
```

- Columns = listings (photo + address header)
- Rows = selected metrics
- Each cell: score value + horizontal bar + medal icon (🥇🥈🥉) for top 3 in each row
- Bottom row: weighted composite across selected metrics → **winner highlighted**
- Bars are color-coded: green (≥75), yellow (50–74), orange (25–49), red (<25)

#### Tab B: Flags View

Each listing gets a card showing auto-generated strengths and concerns:

```
┌─── 142 E 3rd St ─────────────────┐
│  📸  $2,400 · 1BR · Score: 87    │
│                                    │
│  Strengths:                        │
│  🟢 Best transit access (91)      │
│  🟢 Crime trending down 12%      │
│  🟢 Rent-stabilized              │
│                                    │
│  Concerns:                         │
│  🔴 3 open DOB violations         │
│  🔴 Noisiest of the group (45)   │
│  🟡 Management: 14 HPD complaints│
└────────────────────────────────────┘

┌─── 89 Ave A ─────────────────────┐
│  📸  $2,100 · 2BR · Score: 79    │
│                                    │
│  Strengths:                        │
│  🟢 Best deal score (89)          │
│  🟢 Highest amenity access (81)  │
│  🟢 No open building violations  │
│                                    │
│  Concerns:                         │
│  🔴 Crime higher than peers (72) │
│  🟡 Not rent-stabilized          │
└────────────────────────────────────┘
```

- Flags are **relative to the comparison set** — "noisiest of the group" not "noisy in absolute terms"
- "Why it won / why it lost" sentence at the bottom of the winner card:
  *"Won on deal (+10 over avg) and amenities (+5), despite lower crime score (−7)"*

#### Tab C: Radar View

Overlaid spider/radar chart for 2–3 listings on the selected dimensions.

```
              Transit
                 ╱╲
               ╱    ╲
     Noise   ╱  ╱──╲  ╲   Crime
             │ ╱ ●●  ╲ │
             │╱────────╲│
     Amenity  ╲────────╱  Deal
               ╲      ╱
                 ╲  ╱
              Parks
```

- Each listing is a different colored polygon
- Instantly shows the "shape" of each listing's profile
- Legend below with listing names + colors
- Best for 2–3 listings. At 4–5, chart becomes cluttered — show a note suggesting Table or Flags view instead

---

### 6. Map Overlay

**Toggle**: 🗺 icon in top bar. Available from **any** view.

**Behavior**: Full-canvas map replaces the current view. Top bar and sort chips remain visible. Pinned tray remains.

```
┌──────────────────────────────────────────────────┐
│  AptHunt  [Explore|Watchlist|Shortlist]  🔍⚙🗺  │
├──────────────────────────────────────────────────┤
│                                                  │
│        🟢 (92)                                   │
│                    🟡 (64)                       │
│   🟢 (87)                                       │
│              🟢 (79)        🟠 (58)             │
│                       🟢 (84)                    │
│                                                  │
│  Layer toggles (floating chips, top-right):      │
│  [Crime] [Noise] [Flood] [Transit] [Amenities]  │
│                                                  │
├──────────────────────────────────────────────────┤
│  [○ ○ ● ○ ○]  Pinned tray                      │
└──────────────────────────────────────────────────┘
```

- **Pins**: All listings from the active queue, color-coded by composite score (green → yellow → orange → red gradient)
- **Pin label**: Composite score number
- **Tap pin** → mini scorecard popover: photo thumbnail, address, price, composite score, top-3 scores, "Add to Watchlist/Shortlist" action
- **Layer toggles**: Floating chips in top-right corner. Toggle heatmap/marker overlays:
  - Crime: heatmap of crime density
  - Noise: heatmap of 311 noise complaints
  - Flood: flood zone shading
  - Transit: subway station markers
  - Amenities: grocery/gym/restaurant markers
- **Tap 🗺 again** → returns to previous view, preserving state

---

### 7. Filter Sheet

**Trigger**: 🔍 icon in top bar.

**Behavior**: Slides up from bottom (mobile) or slides in from right (desktop). Overlay — doesn't navigate away.

**Contents**:

| Filter | Control |
|--------|---------|
| Price range | Dual-thumb slider ($0 – $10,000) |
| Bedrooms | Chip selector: Studio · 1 · 2 · 3+ |
| Neighborhoods | Searchable multi-select dropdown |
| Rent-stabilized only | Toggle switch |
| Minimum composite score | Single slider (0–100) |
| Minimum score on any dimension | Per-dimension threshold sliders (collapsed by default) |

**Footer**: `Apply` button (dismisses sheet, re-filters active queue) · `Reset` link · Active filter count badge

---

### 8. Preferences / Settings

**Trigger**: ⚙ icon in top bar.

**Behavior**: Full-screen modal or dedicated page.

**Contents**:

#### Priority Ranking
- Drag-to-reorder list of 11 scoring dimensions
- Top 3 get heavier weight in composite score calculation
- Visual weight indicator: ●●● (top 3) / ●● (4–7) / ● (8–11)
- Changes recalculate composite scores across all listings

#### Saved Configs
- Each config = filters + priority ranking
- Name, save, load, delete
- Quick-switch from a dropdown in Settings

#### Account (future)
- Profile, notifications, alert preferences

---

## Pinned Shortlist Tray

**Position**: Bottom of viewport, always visible (~60px).

**Content**: Shortlisted listing thumbnails as circular avatars, max 5 visible + "+n" overflow badge.

```
┌──────────────────────────────────────────────────┐
│  [📸] [📸] [📸] [📸] [📸] +3                    │
└──────────────────────────────────────────────────┘
```

**Interactions**:
- **Tap avatar** → jumps to that listing's expanded card view
- **Drag two avatars together** → triggers Compare mode for those two listings
- **Long-press avatar** → quick actions: "Remove from Shortlist", "Move to Watchlist"
- Tray scrolls horizontally if >5 items

---

## Navigation Flow

```
                    ┌─────────────┐
                    │   EXPLORE   │
                    │  (Feed/Scan)│
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
        ┌───────────┐ ┌─────────┐ ┌─────────┐
        │   Skip    │ │  Save   │ │Shortlist│
        │ (remove   │ │ (add to │ │(add to  │
        │  from     │ │ Watch-  │ │Short-   │
        │  Explore) │ │ list)   │ │list)    │
        └───────────┘ └────┬────┘ └────┬────┘
                           │           │
                           ▼           ▼
                    ┌─────────────┐ ┌─────────────┐
                    │  WATCHLIST  │ │  SHORTLIST   │
                    │   (Grid)   │ │   (Grid)     │
                    └──────┬──────┘ └──────┬──────┘
                           │               │
                           │  Select 2–5   │
                           └───────┬───────┘
                                   ▼
                           ┌─────────────┐
                           │   COMPARE   │
                           │ Table/Flags │
                           │   /Radar    │
                           └─────────────┘

        🗺 MAP OVERLAY — toggleable from ANY view
```

**Key navigation rules**:
- Queue tabs are always accessible in top bar — instant switch
- Feed mode preserves scroll position when you leave and return
- Compare is a modal/overlay — dismiss returns you to the queue you came from
- Map overlay is a toggle — same as Compare, preserves underlying state
- Back button / swipe-back always works intuitively

---

## Transitions & Animations

| Trigger | Animation | Duration |
|---------|-----------|----------|
| Feed → next listing | Horizontal slide with subtle photo parallax | 300ms |
| Feed ↔ Scan toggle | Morph — card shrinks into grid position (or grid tile expands into full card) | 400ms |
| Save action | Card slides off to the right with brief green flash; next card slides in | 350ms |
| Skip action | Card fades down slightly and dims; next card slides in | 250ms |
| Open Compare | Selected cards fly up from grid positions into comparison table columns | 400ms |
| Map toggle on | Canvas cross-fades to map; pins drop in with stagger | 500ms |
| Filter sheet | Slides up from bottom with backdrop dim | 300ms |
| Pinned tray avatar add | New avatar bounces in from the card's position | 300ms |

All animations use **ease-out** curves. Respect `prefers-reduced-motion`.

---

## Color System

| Element | Color | Usage |
|---------|-------|-------|
| Score ≥ 75 | `#22c55e` (green) | Composite badge, bar fill, map pin |
| Score 50–74 | `#eab308` (yellow) | Composite badge, bar fill, map pin |
| Score 25–49 | `#f97316` (orange) | Composite badge, bar fill, map pin |
| Score < 25 | `#ef4444` (red) | Composite badge, bar fill, map pin |
| Green flag | `#22c55e` | Insight/strength indicator |
| Red flag | `#ef4444` | Insight/concern indicator |
| Yellow flag | `#eab308` | Insight/neutral-caution indicator |
| Trend improving ↓ | `#22c55e` | Crime/noise trend arrow |
| Trend worsening ↑ | `#ef4444` | Crime/noise trend arrow |
| Active sort chip | Filled primary | Currently active sort dimension |
| Inactive sort chip | Outlined, muted | Available sort options |

**Theme**: Dark mode default (premium, techy feel). Light mode available via toggle in Settings.

---

## Responsive Behavior

| Breakpoint | Layout Changes |
|------------|----------------|
| Desktop (≥1024px) | Scan grid: 3–4 columns. Compare table fits 5 listings. Map is spacious. |
| Tablet (768–1023px) | Scan grid: 2–3 columns. Compare table fits 3–4 listings. |
| Mobile (<768px) | Scan grid: 2 columns. Compare table scrolls horizontally. Filter sheet is full-width bottom sheet. Sort chips scroll horizontally. Pinned tray shows 3 avatars + overflow. |

Feed mode is inherently mobile-native — single card fills viewport regardless of screen size.

---

## Data Requirements (from backend)

Each listing served to the UI needs:

```json
{
  "id": "listing_123",
  "address": "142 E 3rd St, Apt 4B",
  "neighborhood": "East Village",
  "borough": "Manhattan",
  "price": 2400,
  "bedrooms": 1,
  "bathrooms": 1,
  "photos": ["url1", "url2", "url3"],
  "latitude": 40.7262,
  "longitude": -73.9845,
  "scores": {
    "composite": 87,
    "deal": 72,
    "transit": 91,
    "flood_risk": 95,
    "crime": 88,
    "noise": 45,
    "building_violations": 55,
    "parks": 84,
    "schools": 80,
    "management": 71,
    "amenity": 78,
    "rent_stabilized": true
  },
  "trends": {
    "crime_direction": "improving",
    "crime_ratio": 0.88,
    "noise_direction": "worsening",
    "noise_ratio": 1.15
  },
  "flags": [
    {"type": "green", "text": "Crime down 12% over 6 months"},
    {"type": "green", "text": "Rent-stabilized unit"},
    {"type": "green", "text": "4 subway lines within 0.3 mi"},
    {"type": "red", "text": "3 open DOB violations"},
    {"type": "yellow", "text": "Noise trend slightly rising"},
    {"type": "red", "text": "Management: 14 HPD complaints"}
  ],
  "building": {
    "owner": "XYZ Management LLC",
    "year_built": 1925,
    "total_units": 24,
    "open_violations": 3,
    "total_violations": 12,
    "hpd_complaints_12mo": 14
  }
}
```

---

## Build Order (Recommended)

| Phase | Scope | Dependency |
|-------|-------|------------|
| **1** | API layer: FastAPI serving listings with scores as JSON | Composite score computation |
| **2** | Explore Feed mode: single-card view with scores, swipe navigation, Skip/Save | API |
| **3** | Watchlist & Shortlist grids with multi-select | Queue state management |
| **4** | Compare mode — Table view | Metric selection logic |
| **5** | Explore Scan mode + Feed↔Scan toggle | Grid component reuse from Watchlist |
| **6** | Compare — Flags view | Flag generation logic |
| **7** | Compare — Radar view | Chart library (Chart.js / Recharts) |
| **8** | Map overlay | Mapbox/Leaflet integration |
| **9** | Filter sheet | API filter params |
| **10** | Preferences & priority ranking | Composite recalculation |
| **11** | Pinned tray interactions | Shortlist state |
| **12** | Animations & transitions | All views stable |
| **13** | Dark/light theme | CSS variables |
| **14** | Responsive polish | All components built |

---

## Tech Stack (Recommended)

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Frontend | **Next.js + React** | Swipeable cards, animations, state management — Streamlit can't handle this interaction model |
| Styling | **Tailwind CSS** | Rapid, consistent, dark-mode-friendly utility classes |
| Animations | **Framer Motion** | Production-grade React animation library, handles layout morphs and gestures |
| Charts | **Recharts** (radar) or **Chart.js** | Radar/spider charts for Compare view |
| Maps | **Mapbox GL JS** or **Leaflet** | Layer toggles, heatmaps, pin clustering |
| State | **Zustand** or **React Context** | Lightweight queue state (watchlist, shortlist, filters, preferences) |
| API | **FastAPI** (Python) | Serves listing data + scores from existing SQLite backend |
| Deployment | **Vercel** (frontend) + **Railway/Fly.io** (API) | Fast, free-tier friendly |
