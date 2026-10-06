"""
APTHUNT Admin Dashboard
========================
Streamlit-based admin dashboard for reviewing, filtering, and visualizing
NYC rental listings and their computed scores.

Reads the database at APTHUNT_DB_PATH (default: apthunt.db next to this
file). Build one first with scripts/bootstrap_sample.sh.

Run:
    streamlit run dashboard.py
    # or, if streamlit is not on PATH:
    python3 -m streamlit run dashboard.py
"""

import os
import pathlib
import sqlite3

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = pathlib.Path(
    os.environ.get("APTHUNT_DB_PATH")
    or pathlib.Path(__file__).resolve().parent / "apthunt.db"
)

# All scorer columns present or expected.  As new scorers land, just append
# here — the rest of the dashboard adapts automatically.
SCORE_COLUMNS = [
    "deal_score",
    "unit_amenities_score",
    "transit_score",
    "crime_score",
    "noise_score",
    "building_violations_score",
    "parks_score",
    "schools_score",
    "rent_stabilized_score",
    "management_score",
    "convenience_score",
    "shelter_score",
    "pest_score",
    "greenery_score",
    "bedbug_score",
    "street_danger_score",
    "air_quality_score",
    "road_exposure_score",
]

COMPONENT_COLUMNS = {
    "deal_score": ["comp_median", "comp_set_size", "comp_scope"],
    "unit_amenities_score": ["unit_amenities_premium", "unit_amenities_standard", "unit_amenities_total"],
    "transit_score": ["transit_station_count", "transit_routes_served", "transit_nearest_m"],
    "crime_score": ["crime_felony_count", "crime_misdemeanor_count", "crime_violation_count", "crime_weighted_total", "crime_trend_ratio", "crime_trend_direction"],
    "noise_score": ["noise_complaint_count", "noise_trend_ratio", "noise_trend_direction"],
    "building_violations_score": ["building_violation_count", "building_hpd_class_a", "building_hpd_class_b", "building_hpd_class_c", "building_active_permits", "building_unitsres", "building_violations_per_unit"],
    "parks_score": ["parks_distance_m", "parks_name", "parks_acres"],
    "schools_score": ["school_name", "school_rating"],
    "rent_stabilized_score": ["rent_stabilized", "building_year"],
    "management_score": ["mgmt_owner", "mgmt_owner_buildings", "mgmt_owner_units", "mgmt_complaints", "mgmt_hpd_heat", "mgmt_hpd_plumbing", "mgmt_hpd_paint", "mgmt_hpd_safety", "mgmt_heat_complaints", "mgmt_litigations", "mgmt_evictions", "mgmt_complaints_per_unit"],
    "convenience_score": ["convenience_grocery", "convenience_pharmacy", "convenience_gym", "convenience_laundry", "convenience_dining", "convenience_total"],
    "shelter_score": ["shelter_count", "shelter_nearest_m", "shelter_nearest_name", "shelter_weighted_total", "project_count", "project_nearest_m", "project_nearest_name"],
    "pest_score": ["pest_hpd_count", "pest_rodent_count", "pest_total", "pest_units", "pest_per_unit"],
    "greenery_score": ["greenery_tree_count", "greenery_canopy_score", "greenery_garden_count"],
    "bedbug_score": ["bedbug_filings", "bedbug_infested_total", "bedbug_reinfested_total", "bedbug_rate"],
    "street_danger_score": ["street_danger_injuries", "street_danger_deaths", "street_danger_rate_per_khh"],
    "air_quality_score": ["air_quality_pm25", "air_quality_no2", "air_quality_index"],
    "road_exposure_score": ["road_exposure_hwy_dist_m", "road_exposure_arterial", "road_exposure_truck", "road_exposure_index"],
}

# Pretty labels
SCORE_LABELS = {
    "deal_score": "Deal",
    "unit_amenities_score": "Unit Amenities",
    "transit_score": "Transit",
    "crime_score": "Crime",
    "noise_score": "Noise",
    "building_violations_score": "Building Violations",
    "parks_score": "Parks/Green Space",
    "schools_score": "School Quality",
    "rent_stabilized_score": "Rent Stabilized",
    "management_score": "Management",
    "convenience_score": "Convenience",
    "shelter_score": "Shelters & Projects",
    "pest_score": "Pests",
    "greenery_score": "Greenery",
    "bedbug_score": "Bedbugs",
    "street_danger_score": "Street Safety",
    "air_quality_score": "Air Quality",
    "road_exposure_score": "Road Noise",
}

# What each score measures — shown as help text on the Score Analysis tab.
# Higher is always better. How the raw measure becomes a 0-100 score is in
# each scorer's docstring (apthunt/scoring/).
SCORE_DESCRIPTIONS = {
    "deal_score": "Value against comparable listings (same neighborhood and bed count): price, $/sqft, size, estimated tenant tenure and fresh price cuts. Tenure estimated from listing relist history — rapid relists clustered to avoid false short-lease signals.",
    "unit_amenities_score": "In-unit and building amenities from the listing. Premium ones (washer/dryer, dishwasher, private outdoor space) weigh more than standard ones (doorman, elevator, gym).",
    "transit_score": "Walk to the nearest subway entrance (exponential decay, 400 m scale), scaled by how many lines stop within 800 m, plus a small bonus for bus routes within 300 m.",
    "crime_score": "NYPD complaints within 400 m, weighted by severity, recency and distance, plus shootings, per 1,000 households. Includes trend.",
    "noise_score": "311 noise complaints within 150 m (12 mo), weighted by recency and distance, per 1,000 households. Includes trend.",
    "building_violations_score": "Open DOB and HPD violations on the building, weighted by severity, per residential unit.",
    "parks_score": "Size-weighted proximity. Each park scored by (1 \u2212 dist/reach) \u00d7 quality. Big parks reach further and score higher; best park wins.",
    "schools_score": "Best nearby public high school by attendance rate and share of students who feel safe (within ~1.5 km).",
    "rent_stabilized_score": "Binary flag: likely rent-stabilized if built pre-1974 with 6+ units (PLUTO heuristic).",
    "management_score": "HPD complaints across the owner's portfolio per unit, plus the building's own heat complaints, HPD litigation and evictions.",
    "convenience_score": "Groceries, supermarkets, pharmacies, gyms, laundromats and restaurants within 500 m, distance-weighted with diminishing returns per category.",
    "shelter_score": "311 encampment complaints within 200 m, and homeless-services facilities and NYCHA developments within 800 m, distance-weighted. Inverted: less = better.",
    "pest_score": "DOHMH rodent inspection results and 311 rodent complaints within 100 m, plus the building's own HPD pest complaints per unit.",
    "greenery_score": "Street trees and their canopy within 200 m and community gardens within 500 m, distance-weighted. Parks are a separate dimension.",
    "bedbug_score": "The building's annual bedbug filings (infested and re-infested units, recent years weigh more) per unit.",
    "street_danger_score": "Pedestrian and cyclist crash injuries and deaths within 300 m (12 mo), weighted by distance and recency, per 1,000 households.",
    "air_quality_score": "PM2.5 and NO2 for the community district (NYC Community Air Survey), nudged by the block's road exposure. Absolute scale, not a percentile.",
    "road_exposure_score": "Road noise: distance to highways (less when rows of buildings stand in between), arterial traffic within 150 m, truck routes, elevated trains, bus corridors and firehouses.",
}

# Scoring method on a database WITHOUT baselines — controls how scores are
# displayed and colored. Once scripts/build_baseline.py has frozen a
# baseline for a dimension, that dimension is a percentile of the baseline
# distribution instead (see score_method below).
SCORE_METHODS = {
    "deal_score": "zscore",
    "unit_amenities_score": "percentile",
    "transit_score": "linear",
    "crime_score": "median_norm",
    "noise_score": "median_norm",
    "building_violations_score": "percentile",
    "parks_score": "linear",
    "schools_score": "absolute",
    "rent_stabilized_score": "binary",
    "management_score": "percentile",
    "convenience_score": "absolute",
    "shelter_score": "median_norm",
    "pest_score": "percentile",
    "greenery_score": "absolute",
    "bedbug_score": "absolute",
    "street_danger_score": "absolute",
    "air_quality_score": "absolute",
    "road_exposure_score": "absolute",
}

# These never go through a baseline: transit and air quality are absolute
# by design, rent stabilization is a flag.
NEVER_BASELINED = {"transit_score", "air_quality_score", "rent_stabilized_score"}

# Human-readable component labels
COMPONENT_LABELS = {
    "comp_median": "Comp-set median rent",
    "comp_set_size": "Comp-set size",
    "comp_scope": "Comp scope",
    "transit_station_count": "Stations within 800 m",
    "transit_routes_served": "Unique routes",
    "transit_nearest_m": "Nearest station (m)",
    "flood_firm07": "FIRM 2007 flood zone",
    "flood_pfirm15": "PFIRM 2015 flood zone",
    "crime_felony_count": "Felonies (12 mo)",
    "crime_misdemeanor_count": "Misdemeanors (12 mo)",
    "crime_violation_count": "Violations (12 mo)",
    "crime_weighted_total": "Weighted total",
    "crime_trend_ratio": "Crime trend ratio",
    "crime_trend_direction": "Crime trend",
    "noise_complaint_count": "Noise complaints",
    "noise_trend_ratio": "Noise trend ratio",
    "noise_trend_direction": "Noise trend",
    "building_violation_count": "Active DOB violations",
    "building_hpd_class_a": "HPD Class A violations",
    "building_hpd_class_b": "HPD Class B violations",
    "building_hpd_class_c": "HPD Class C violations (hazardous)",
    "building_active_permits": "Active DOB permits",
    "building_unitsres": "Residential units",
    "building_violations_per_unit": "Weighted violations per unit",
    "parks_distance_m": "Distance to best park (m)",
    "parks_name": "Best scoring park",
    "parks_acres": "Park size (acres)",
    "school_name": "Best nearby school",
    "school_rating": "School quality composite",
    "rent_stabilized": "Likely rent-stabilized",
    "building_year": "Year built",
    "mgmt_owner": "Owner / management co.",
    "mgmt_owner_buildings": "Owner portfolio (buildings)",
    "mgmt_owner_units": "Owner portfolio (units)",
    "mgmt_complaints": "HPD complaints (12 mo)",
    "mgmt_hpd_heat": "Heat / hot water complaints",
    "mgmt_hpd_plumbing": "Plumbing complaints",
    "mgmt_hpd_paint": "Paint / plaster complaints",
    "mgmt_hpd_safety": "Safety complaints",
    "mgmt_heat_complaints": "311 heat complaints (area)",
    "mgmt_litigations": "HPD litigations (open)",
    "mgmt_evictions": "Eviction filings (building)",
    "mgmt_complaints_per_unit": "Complaints per unit",
    "convenience_grocery": "Grocery / convenience",
    "convenience_pharmacy": "Pharmacies",
    "convenience_gym": "Gyms / fitness",
    "convenience_laundry": "Laundromats",
    "convenience_dining": "Restaurants & caf\u00e9s",
    "convenience_total": "Weighted convenience total",
    "unit_amenities_premium": "Premium amenities",
    "unit_amenities_standard": "Standard amenities",
    "unit_amenities_total": "Weighted amenity total",
    "shelter_count": "Shelters within 800 m",
    "shelter_nearest_m": "Nearest shelter (m)",
    "shelter_nearest_name": "Nearest shelter",
    "shelter_weighted_total": "Distance-weighted total",
    "project_count": "NYCHA buildings within 800 m",
    "project_nearest_m": "Nearest project (m)",
    "project_nearest_name": "Nearest NYCHA development",
    "pest_hpd_count": "HPD pest complaints (building)",
    "pest_rodent_count": "311 rodent complaints (100 m)",
    "pest_total": "Total pest reports",
    "pest_units": "Residential units",
    "pest_per_unit": "Pests per unit",
    "greenery_tree_count": "Street trees within 200 m",
    "greenery_canopy_score": "Canopy score (diameter-weighted)",
    "greenery_garden_count": "Community gardens within 500 m",
    "greenery_park_count": "Parks within 500 m",
    "bedbug_filings": "Bedbug filings on record",
    "bedbug_infested_total": "Infested units (all filings)",
    "bedbug_reinfested_total": "Re-infested units (all filings)",
    "bedbug_rate": "Weighted bedbug rate per unit",
    "street_danger_injuries": "Pedestrian + cyclist injuries (300 m, 12 mo)",
    "street_danger_deaths": "Pedestrian + cyclist deaths (300 m, 12 mo)",
    "street_danger_rate_per_khh": "Weighted casualties per 1,000 households",
    "air_quality_pm25": "PM2.5 (\u00b5g/m\u00b3, annual)",
    "air_quality_no2": "NO2 (ppb, annual)",
    "air_quality_index": "Combined air index",
    "road_exposure_hwy_dist_m": "Nearest highway (m)",
    "road_exposure_arterial": "Arterial traffic density",
    "road_exposure_truck": "Truck-route proximity",
    "road_exposure_index": "Combined road-noise index",
}


# ---------------------------------------------------------------------------
# Data loading (cached so Streamlit doesn't re-query on every interaction)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def load_listings() -> pd.DataFrame:
    """Load all listings from the SQLite database into a DataFrame."""
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query("SELECT * FROM listings", conn)
    conn.close()

    # Coerce types
    for col in SCORE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["beds"] = pd.to_numeric(df["beds"], errors="coerce")
    df["baths"] = pd.to_numeric(df["baths"], errors="coerce")
    df["sqft"] = pd.to_numeric(df["sqft"], errors="coerce")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")

    return df


def active_score_columns(df: pd.DataFrame) -> list[str]:
    """Return score columns that actually exist in the DB and have data."""
    return [c for c in SCORE_COLUMNS if c in df.columns and df[c].notna().any()]


@st.cache_data(ttl=60)
def baselined_score_columns() -> set[str]:
    """Score columns whose dimension has a frozen baseline distribution."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute("SELECT dimension FROM baseline_dist").fetchall()
    except sqlite3.OperationalError:
        rows = []  # no baseline built yet
    finally:
        conn.close()
    return {f"{dimension}_score" for (dimension,) in rows} - NEVER_BASELINED


def score_method(col: str) -> str:
    """How a score column is computed in THIS database."""
    if col in baselined_score_columns():
        return "percentile"
    return SCORE_METHODS.get(col, "absolute")


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="APTHUNT Admin",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🏠 APTHUNT Admin Dashboard")

if not DB_PATH.is_file():
    st.error(
        f"No database at {DB_PATH}. Build one with "
        "`./scripts/bootstrap_sample.sh`, or point APTHUNT_DB_PATH at an existing file."
    )
    st.stop()

df_raw = load_listings()


# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

st.sidebar.header("Filters")

# Borough
boroughs = sorted(df_raw["borough"].dropna().unique())
sel_boroughs = st.sidebar.multiselect("Borough", boroughs, default=boroughs)

# Neighborhood
available_neighborhoods = sorted(
    df_raw[df_raw["borough"].isin(sel_boroughs)]["neighborhood"].dropna().unique()
)
sel_neighborhoods = st.sidebar.multiselect(
    "Neighborhood",
    available_neighborhoods,
    default=[],
    help="Leave empty = all neighborhoods",
)

# Beds
bed_options = sorted(df_raw["beds"].dropna().unique())
bed_labels = {0: "Studio", 1: "1 BR", 2: "2 BR", 3: "3 BR", 4: "4 BR", 5: "5 BR"}
sel_beds = st.sidebar.multiselect(
    "Bedrooms",
    bed_options,
    format_func=lambda x: bed_labels.get(int(x), f"{int(x)} BR"),
    default=[],
    help="Leave empty = all",
)

# Price
price_min, price_max = int(df_raw["price"].min()), int(df_raw["price"].max())
sel_price = st.sidebar.slider(
    "Price range ($)",
    min_value=price_min,
    max_value=price_max,
    value=(price_min, price_max),
    step=100,
    format="$%d",
)

# Score thresholds
st.sidebar.markdown("---")
st.sidebar.subheader("Score Filters")

score_filters: dict[str, tuple[float, float]] = {}
binary_filters: dict[str, str] = {}   # col -> "only_yes" | "only_no" | "all"
scores = active_score_columns(df_raw)
for col in scores:
    label = SCORE_LABELS.get(col, col)
    method = score_method(col)

    if method == "binary":
        # Checkboxes for binary scores instead of a slider
        choice = st.sidebar.radio(
            label,
            ["All", "Yes only", "No only"],
            index=0,
            horizontal=True,
            key=f"bin_{col}",
        )
        if choice == "Yes only":
            binary_filters[col] = "only_yes"
        elif choice == "No only":
            binary_filters[col] = "only_no"
    else:
        lo, hi = float(df_raw[col].min()), float(df_raw[col].max())
        if lo == hi:
            continue
        desc = SCORE_DESCRIPTIONS.get(col, "")
        score_filters[col] = st.sidebar.slider(
            f"{label} score",
            min_value=lo,
            max_value=hi,
            value=(lo, hi),
            step=1.0,
            help=desc,
        )


# ---------------------------------------------------------------------------
# Apply filters
# ---------------------------------------------------------------------------

df = df_raw.copy()
df = df[df["borough"].isin(sel_boroughs)]
if sel_neighborhoods:
    df = df[df["neighborhood"].isin(sel_neighborhoods)]
if sel_beds:
    df = df[df["beds"].isin(sel_beds)]
df = df[(df["price"] >= sel_price[0]) & (df["price"] <= sel_price[1])]

for col, (lo, hi) in score_filters.items():
    df = df[df[col].between(lo, hi) | df[col].isna()]

for col, mode in binary_filters.items():
    if mode == "only_yes":
        df = df[(df[col] == 100)]
    elif mode == "only_no":
        df = df[(df[col] == 0) | df[col].isna()]


# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------

st.markdown("---")

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Listings", f"{len(df):,}")
k2.metric("Avg Rent", f"${df['price'].mean():,.0f}" if len(df) else "—")
k3.metric("Median Rent", f"${df['price'].median():,.0f}" if len(df) else "—")
if "deal_score" in df.columns and df["deal_score"].notna().any():
    k4.metric("Avg Deal Score", f"{df['deal_score'].mean():.1f}")
else:
    k4.metric("Avg Deal Score", "—")
if "flood_risk_score" in df.columns and df["flood_risk_score"].notna().any():
    flood_pct = (df["flood_risk_score"] == 0).sum()
    k5.metric("In Flood Zone", f"{flood_pct}")
else:
    k5.metric("In Flood Zone", "—")


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_map, tab_scores, tab_neighborhood, tab_table, tab_detail = st.tabs(
    ["📍 Map", "📊 Score Analysis", "🏘️ Neighborhoods", "📋 Data Table", "🔎 Listing Detail"]
)


# ---- MAP TAB ----
with tab_map:
    st.subheader("Listing Map")

    map_df = df.dropna(subset=["lat", "lon"]).copy()
    if map_df.empty:
        st.info("No listings with coordinates to display.")
    else:
        color_by = st.selectbox(
            "Color by",
            ["price"] + scores,
            format_func=lambda x: SCORE_LABELS.get(x, x.replace("_", " ").title()),
            key="map_color",
        )

        fig = px.scatter_map(
            map_df,
            lat="lat",
            lon="lon",
            color=color_by,
            size="price",
            size_max=15,
            hover_name="address",
            hover_data={
                "neighborhood": True,
                "price": ":$,.0f",
                "beds": True,
                "deal_score": ":.1f",
            },
            color_continuous_scale="RdYlGn" if "score" in color_by else "Viridis",
            zoom=11,
            height=650,
        )
        fig.update_layout(margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig)


# ---- SCORES TAB ----
with tab_scores:
    st.subheader("Score Distributions")

    if not scores:
        st.info("No scores computed yet. Run `python3 run_scores.py` first.")
    else:
        # Distribution histograms — one row per active scorer
        cols = st.columns(min(len(scores), 3))
        for i, col_name in enumerate(scores):
            with cols[i % len(cols)]:
                label = SCORE_LABELS.get(col_name, col_name)
                method = score_method(col_name)
                desc = SCORE_DESCRIPTIONS.get(col_name, "")
                scored = df[col_name].dropna()
                if scored.empty:
                    st.caption(f"{label}: no data")
                    continue

                if method == "binary":
                    # Show a bar chart (Yes / No) for binary scores
                    yes_count = int((scored == 100).sum())
                    no_count = len(scored) - yes_count
                    bin_df = pd.DataFrame({"Category": ["Yes", "No"], "Count": [yes_count, no_count]})
                    fig = px.bar(
                        bin_df, x="Category", y="Count",
                        title=f"{label}",
                        color="Category",
                        color_discrete_map={"Yes": "#2ecc71", "No": "#e74c3c"},
                    )
                    fig.update_layout(
                        showlegend=False,
                        height=300,
                        margin=dict(l=20, r=20, t=40, b=20),
                    )
                else:
                    # Histogram for continuous scores
                    method_tag = {
                        "percentile": (
                            "Baseline Percentile"
                            if col_name in baselined_score_columns() else "Percentile"
                        ),
                        "zscore": "Z-Score",
                        "median_norm": "Median-Normalized",
                        "linear": "Distance-Based",
                    }.get(method, "")
                    title = f"{label} ({method_tag})" if method_tag else label
                    fig = px.histogram(
                        scored,
                        nbins=30,
                        title=title,
                        labels={"value": label, "count": "Listings"},
                        color_discrete_sequence=["#636EFA"],
                    )
                    fig.update_layout(
                        showlegend=False,
                        height=300,
                        margin=dict(l=20, r=20, t=40, b=20),
                    )

                st.plotly_chart(fig)
                if desc:
                    st.caption(desc)

        # Score correlation scatter (if 2+ scores available)
        real_scores = [s for s in scores if df[s].notna().nunique() > 1]
        if len(real_scores) >= 2:
            st.markdown("---")
            st.subheader("Score Correlations")
            c1, c2 = st.columns(2)
            x_score = c1.selectbox(
                "X axis",
                real_scores,
                index=0,
                format_func=lambda x: SCORE_LABELS.get(x, x),
                key="corr_x",
            )
            y_score = c2.selectbox(
                "Y axis",
                real_scores,
                index=min(1, len(real_scores) - 1),
                format_func=lambda x: SCORE_LABELS.get(x, x),
                key="corr_y",
            )
            scatter_df = df.dropna(subset=[x_score, y_score])
            fig = px.scatter(
                scatter_df,
                x=x_score,
                y=y_score,
                color="borough",
                hover_name="address",
                hover_data={"price": ":$,.0f", "beds": True},
                title=f"{SCORE_LABELS.get(x_score, x_score)} vs {SCORE_LABELS.get(y_score, y_score)}",
                height=450,
            )
            st.plotly_chart(fig)

        # Box plots — score by beds
        st.markdown("---")
        st.subheader("Scores by Bedroom Count")
        score_for_box = st.selectbox(
            "Score",
            scores,
            format_func=lambda x: SCORE_LABELS.get(x, x),
            key="box_score",
        )
        box_df = df.dropna(subset=[score_for_box]).copy()
        box_df["beds_label"] = box_df["beds"].apply(
            lambda b: bed_labels.get(int(b), f"{int(b)} BR") if pd.notna(b) else "?"
        )
        fig = px.box(
            box_df,
            x="beds_label",
            y=score_for_box,
            color="borough",
            title=f"{SCORE_LABELS.get(score_for_box, score_for_box)} by Bedrooms",
            height=400,
        )
        st.plotly_chart(fig)


# ---- NEIGHBORHOOD TAB ----
with tab_neighborhood:
    st.subheader("Neighborhood Breakdown")

    agg_dict: dict[str, tuple[str, str]] = {
        "listings": ("price", "count"),
        "avg_price": ("price", "mean"),
        "median_price": ("price", "median"),
    }
    for s in scores:
        agg_dict[f"avg_{s}"] = (s, "mean")

    hood_df = (
        df.groupby("neighborhood")
        .agg(**agg_dict)
        .reset_index()
        .sort_values("listings", ascending=False)
    )

    # Format for display
    hood_display = hood_df.copy()
    hood_display["avg_price"] = hood_display["avg_price"].map("${:,.0f}".format)
    hood_display["median_price"] = hood_display["median_price"].map("${:,.0f}".format)
    for s in scores:
        col = f"avg_{s}"
        hood_display[col] = hood_display[col].map("{:.1f}".format)

    st.dataframe(
        hood_display,
        width="stretch",
        hide_index=True,
        height=500,
    )

    # Bar chart — top neighborhoods by selected metric
    st.markdown("---")
    metric_options = ["listings", "avg_price", "median_price"] + [f"avg_{s}" for s in scores]
    metric_choice = st.selectbox(
        "Rank neighborhoods by",
        metric_options,
        format_func=lambda x: x.replace("_", " ").title(),
    )
    top_n = st.slider("Top N", 5, min(30, len(hood_df)), 15)
    chart_df = hood_df.nlargest(top_n, metric_choice)

    fig = px.bar(
        chart_df,
        x="neighborhood",
        y=metric_choice,
        title=f"Top {top_n} Neighborhoods by {metric_choice.replace('_', ' ').title()}",
        color=metric_choice,
        color_continuous_scale="Blues",
        height=400,
    )
    fig.update_layout(xaxis_tickangle=-45)
    st.plotly_chart(fig)

    # Price vs Score scatter by neighborhood
    if "avg_deal_score" in hood_df.columns:
        st.markdown("---")
        st.subheader("Neighborhood: Price vs Deal Quality")
        fig = px.scatter(
            hood_df,
            x="median_price",
            y="avg_deal_score",
            size="listings",
            hover_name="neighborhood",
            title="Median Price vs Avg Deal Score by Neighborhood",
            labels={"median_price": "Median Price ($)", "avg_deal_score": "Avg Deal Score"},
            height=450,
        )
        st.plotly_chart(fig)


# ---- DATA TABLE TAB ----
with tab_table:
    st.subheader("Listings")

    # Column picker
    display_cols = [
        "address", "unit", "neighborhood", "borough",
        "price", "beds", "baths",
    ] + scores + ["url"]
    available = [c for c in display_cols if c in df.columns]

    sort_col = st.selectbox("Sort by", available, index=available.index("price") if "price" in available else 0)
    sort_asc = st.checkbox("Ascending", value=True)

    show_df = df[available].sort_values(sort_col, ascending=sort_asc).reset_index(drop=True)

    # Format price
    if "price" in show_df.columns:
        show_df["price"] = show_df["price"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "")

    # Make URL clickable
    if "url" in show_df.columns:
        show_df["url"] = show_df["url"].apply(
            lambda u: u if pd.notna(u) else ""
        )

    # Build column configs dynamically for all active scores
    col_config = {
        "url": st.column_config.LinkColumn("Listing", display_text="View →"),
    }
    for s in scores:
        label = SCORE_LABELS.get(s, s)
        method = score_method(s)
        if method == "binary":
            col_config[s] = st.column_config.CheckboxColumn(
                label, help=SCORE_DESCRIPTIONS.get(s, ""),
            )
            # Convert 100→True, 0→False for checkbox display
            if s in show_df.columns:
                show_df[s] = show_df[s].apply(
                    lambda x: True if x == 100 else (False if pd.notna(x) else None)
                )
        else:
            col_config[s] = st.column_config.ProgressColumn(
                label, min_value=0, max_value=100,
                format="%.0f" if method in ("linear", "median_norm") else "%.1f",
                help=SCORE_DESCRIPTIONS.get(s, ""),
            )

    st.dataframe(
        show_df,
        width="stretch",
        hide_index=True,
        height=700,
        column_config=col_config,
    )

    st.caption(f"Showing {len(show_df):,} listings")

    # CSV download
    csv = df[available].to_csv(index=False)
    st.download_button("⬇ Download CSV", csv, "apthunt_listings.csv", "text/csv")


# ---- DETAIL TAB ----
with tab_detail:
    st.subheader("Listing Detail")

    # Search by address
    search = st.text_input("Search by address or neighborhood", "")
    if search:
        matches = df[
            df["address"].str.contains(search, case=False, na=False)
            | df["neighborhood"].str.contains(search, case=False, na=False)
        ]
    else:
        matches = df.head(20)

    if matches.empty:
        st.info("No listings match your search.")
    else:
        listing_options = {
            f"{r['address']}, {r.get('unit', '')} — {r['neighborhood']} (${r['price']:,.0f})": r["id"]
            for _, r in matches.iterrows()
        }
        selected_label = st.selectbox("Select listing", list(listing_options.keys()))
        selected_id = listing_options[selected_label]
        row = df[df["id"] == selected_id].iloc[0]

        # Layout
        col_info, col_scores = st.columns([1, 1])

        with col_info:
            st.markdown(f"### {row['address']}, {row.get('unit', '')}")
            st.markdown(f"**{row['neighborhood']}**, {row.get('borough', '').title()}")
            st.markdown(f"**${row['price']:,.0f}/mo** · {bed_labels.get(int(row['beds']), str(int(row['beds'])))} · {row['baths']:.1f} bath")
            if pd.notna(row.get("sqft")) and row["sqft"] > 0:
                st.markdown(f"**{int(row['sqft'])} sqft** (${row['price']/row['sqft']:.0f}/sqft)")
            if row.get("no_fee"):
                st.success("No Fee")
            if row.get("months_free") and row["months_free"] > 0:
                st.info(f"{row['months_free']} months free")
            if pd.notna(row.get("available_at")):
                st.markdown(f"📅 Available: {row['available_at']}")
            if pd.notna(row.get("url")):
                st.markdown(f"[View listing →]({row['url']})")

        with col_scores:
            st.markdown("#### Scores")
            for s in scores:
                val = row.get(s)
                if pd.notna(val):
                    label = SCORE_LABELS.get(s, s)
                    method = score_method(s)

                    if method == "binary":
                        # Binary scores: show ✅/❌ with Yes/No
                        if val == 100:
                            st.markdown(f"✅ **{label}**: Yes")
                        else:
                            st.markdown(f"❌ **{label}**: No")
                    elif method == "percentile":
                        # Percentile scores: 50 = median
                        if val >= 65:
                            color = "🟢"
                        elif val >= 35:
                            color = "🟡"
                        else:
                            color = "🔴"
                        pctile = int(round(val))
                        st.markdown(f"{color} **{label}**: percentile {pctile}")
                    else:
                        # Continuous scores (z-score, linear, median-norm)
                        if val >= 60:
                            color = "🟢"
                        elif val >= 30:
                            color = "🟡"
                        else:
                            color = "🔴"
                        st.markdown(f"{color} **{label}**: {val:.1f} / 100")

            # Components
            st.markdown("---")
            st.markdown("#### Score Components")
            for s in scores:
                comps = COMPONENT_COLUMNS.get(s, [])
                visible = [(c, row.get(c)) for c in comps if pd.notna(row.get(c)) and row.get(c) != ""]
                if not visible:
                    continue
                s_label = SCORE_LABELS.get(s, s)
                st.caption(f"**{s_label}**")
                for c, val in visible:
                    pretty = COMPONENT_LABELS.get(c, c.replace("_", " ").title())
                    # Format numbers nicely
                    if isinstance(val, float) and val == int(val):
                        val = int(val)
                    st.caption(f"  {pretty}: {val}")


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.markdown("---")
st.caption(
    f"APTHUNT Admin · {len(df_raw):,} listings in database · "
    f"{len(scores)} active scorer(s) · "
    f"Data: {DB_PATH.name}"
)
