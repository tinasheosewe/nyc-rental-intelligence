"""
APTHUNT Admin Dashboard
========================
Streamlit-based admin dashboard for reviewing, filtering, and visualizing
NYC rental listings and their computed scores.

Run:
    streamlit run dashboard.py
    # or, if streamlit is not on PATH:
    python3 -m streamlit run dashboard.py
"""

import pathlib
import sqlite3

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = pathlib.Path(__file__).resolve().parent / "apthunt.db"

# All scorer columns present or expected.  As new scorers land, just append
# here — the rest of the dashboard adapts automatically.
SCORE_COLUMNS = [
    "deal_score",
    "transit_score",
    "flood_risk_score",
    "crime_score",
    "noise_score",
    "building_violations_score",
    "parks_score",
    "schools_score",
]

COMPONENT_COLUMNS = {
    "deal_score": ["comp_median", "comp_set_size", "comp_scope"],
    "transit_score": ["transit_station_count", "transit_routes_served", "transit_nearest_m"],
    "flood_risk_score": ["flood_firm07", "flood_pfirm15"],
    "crime_score": ["crime_felony_count", "crime_misdemeanor_count", "crime_violation_count", "crime_weighted_total"],
    "noise_score": ["noise_complaint_count", "noise_rodent_count", "noise_heat_count"],
    "building_violations_score": ["building_violation_count", "building_unitsres", "building_violations_per_unit"],
    "parks_score": ["parks_distance_m", "parks_name"],
    "schools_score": ["school_name", "school_rating"],
}

# Pretty labels
SCORE_LABELS = {
    "deal_score": "Deal",
    "transit_score": "Transit",
    "flood_risk_score": "Flood Risk",
    "crime_score": "Crime",
    "noise_score": "Noise",
    "building_violations_score": "Building Violations",
    "parks_score": "Parks/Green Space",
    "schools_score": "School Quality",
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
scores = active_score_columns(df_raw)
for col in scores:
    label = SCORE_LABELS.get(col, col)
    lo, hi = float(df_raw[col].min()), float(df_raw[col].max())
    if lo == hi:
        continue
    score_filters[col] = st.sidebar.slider(
        f"{label} score",
        min_value=lo,
        max_value=hi,
        value=(lo, hi),
        step=1.0,
    )

# Flood filter shortcut
flood_only_safe = st.sidebar.checkbox("Exclude flood zones", value=False)


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

if flood_only_safe:
    df = df[(df["flood_risk_score"] == 100) | df["flood_risk_score"].isna()]


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
if "flood_risk_score" in df.columns:
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
                "flood_risk_score": ":.0f",
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
                scored = df[col_name].dropna()
                if scored.empty:
                    st.caption(f"{label}: no data")
                    continue
                fig = px.histogram(
                    scored,
                    nbins=30,
                    title=f"{label} Score Distribution",
                    labels={"value": label, "count": "Listings"},
                    color_discrete_sequence=["#636EFA"],
                )
                fig.update_layout(
                    showlegend=False,
                    height=300,
                    margin=dict(l=20, r=20, t=40, b=20),
                )
                st.plotly_chart(fig)

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

    st.dataframe(
        show_df,
        width="stretch",
        hide_index=True,
        height=700,
        column_config={
            "url": st.column_config.LinkColumn("Listing", display_text="View →"),
            "deal_score": st.column_config.ProgressColumn(
                "Deal", min_value=0, max_value=100, format="%.1f"
            ),
            "transit_score": st.column_config.ProgressColumn(
                "Transit", min_value=0, max_value=100, format="%.0f"
            ),
            "flood_risk_score": st.column_config.ProgressColumn(
                "Flood Safety", min_value=0, max_value=100, format="%.0f"
            ),
        },
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
                    # Color: green ≥60, yellow 30-60, red <30
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
                for c in comps:
                    val = row.get(c)
                    if pd.notna(val) and val != "":
                        st.caption(f"{c}: {val}")


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.markdown("---")
st.caption(
    f"APTHUNT Admin · {len(df_raw):,} listings in database · "
    f"{len(scores)} active scorer(s) · "
    f"Data: {DB_PATH.name}"
)
