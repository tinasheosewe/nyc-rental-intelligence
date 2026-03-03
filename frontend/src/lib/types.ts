/**
 * Core domain types for AptHunt.
 *
 * Mirrors the API response models exactly.
 * Single source of truth for the frontend.
 */

// ── Score dimensions ───────────────────────────────────────────

export const SCORE_DIMENSIONS = [
  "deal",
  "unit_amenities",
  "transit",
  "crime",
  "noise",
  "building_violations",
  "parks",
  "schools",
  "management",
  "convenience",
  "shelter",
  "pest",
  "greenery",
] as const;

export type ScoreDimension = (typeof SCORE_DIMENSIONS)[number];

export const DIMENSION_LABELS: Record<ScoreDimension, string> = {
  deal: "Deal",
  unit_amenities: "Amenities",
  transit: "Transit",
  crime: "Crime",
  noise: "Noise",
  building_violations: "Building Violations",
  parks: "Parks",
  schools: "Schools",
  management: "Management",
  convenience: "Convenience",
  shelter: "Shelters & Projects",
  pest: "Pests",
  greenery: "Greenery",
};

// ── Dimension-specific score tier labels ───────────────────────
// 5 tiers: [90–100, 75–89, 60–74, 40–59, 0–39]

export type ScoreTierLabels = [string, string, string, string, string];

export const DIMENSION_TIER_LABELS: Record<ScoreDimension, ScoreTierLabels> = {
  deal:                ["Steal",            "Great Deal",       "Fair Price",      "Pricey",           "Overpriced"],
  unit_amenities:      ["Fully Loaded",     "Well Equipped",    "Good Features",   "Basic",            "Bare Bones"],
  transit:             ["Car-Free",         "Excellent Transit","Good Transit",    "Limited Transit",  "Car Needed"],
  crime:               ["Very Safe",        "Safe",             "Moderate Risk",   "Some Risk",        "High Risk"],
  noise:               ["Very Quiet",       "Quiet",            "Moderate Noise",  "Noisy",            "Very Noisy"],
  building_violations: ["Pristine",         "Well Maintained",  "Some Issues",     "Concerns",         "Many Violations"],
  parks:               ["Park Paradise",    "Great Parks",      "Good Access",     "Few Parks",        "No Parks Nearby"],
  schools:             ["Top Schools",      "Great Schools",    "Good Schools",    "Few Options",      "Limited Schools"],
  management:          ["Excellent Mgmt",   "Good Mgmt",       "Average Mgmt",    "Poor Mgmt",        "Bad Mgmt"],
  convenience:         ["Everything Nearby", "Well Served",     "Decent Options",  "Limited",          "Sparse"],
  shelter:             ["Very Low Presence", "Low Presence",    "Some Presence",   "Notable Presence", "High Presence"],
  pest:                ["No Issues",        "Minimal Issues",   "Some Reports",    "Pest Concerns",    "Major Problems"],
  greenery:            ["Lush",             "Very Green",       "Green",           "Some Greenery",    "Sparse"],
};

// ── Score groups ───────────────────────────────────────────────

export const SCORE_GROUP_KEYS = [
  "value",
  "access",
  "neighborhood",
  "safety",
  "building",
] as const;

export type ScoreGroupKey = (typeof SCORE_GROUP_KEYS)[number];

export interface ScoreGroup {
  key: ScoreGroupKey;
  label: string;
  icon: string;
  dimensions: ScoreDimension[];
  description: string;
}

export const SCORE_GROUPS: ScoreGroup[] = [
  {
    key: "value",
    label: "Value",
    icon: "💰",
    dimensions: ["deal", "unit_amenities"],
    description: "Deal Quality, Unit Features",
  },
  {
    key: "access",
    label: "Access",
    icon: "🚇",
    dimensions: ["transit"],
    description: "Transit access",
  },
  {
    key: "neighborhood",
    label: "Neighborhood",
    icon: "🌳",
    dimensions: ["convenience", "parks", "greenery", "schools"],
    description: "Convenience, Parks, Greenery, Schools",
  },
  {
    key: "safety",
    label: "Safety",
    icon: "🛡️",
    dimensions: ["crime", "noise", "shelter"],
    description: "Crime, Noise, Shelters",
  },
  {
    key: "building",
    label: "Building",
    icon: "🏢",
    dimensions: ["building_violations", "management", "pest"],
    description: "Violations, Management, Pests",
  },
];

export const GROUP_LABELS: Record<ScoreGroupKey, string> = {
  value: "Value",
  access: "Access",
  neighborhood: "Neighborhood",
  safety: "Safety",
  building: "Building",
};

export const DEFAULT_GROUP_PRIORITIES: ScoreGroupKey[] = [
  "value",
  "safety",
  "building",
  "neighborhood",
  "access",
];

/** Lookup from group key → ScoreGroup config. */
export const GROUP_BY_KEY: Record<ScoreGroupKey, ScoreGroup> =
  Object.fromEntries(SCORE_GROUPS.map((g) => [g.key, g])) as Record<ScoreGroupKey, ScoreGroup>;

/**
 * Return score groups with "schools" filtered out when kidsMode is off.
 * When kidsMode is true, returns the original SCORE_GROUPS unchanged.
 */
export function getEffectiveGroups(kidsMode: boolean): ScoreGroup[] {
  if (kidsMode) return SCORE_GROUPS;
  return SCORE_GROUPS.map((g) => {
    if (g.key !== "neighborhood") return g;
    const dims = g.dimensions.filter((d) => d !== "schools");
    return {
      ...g,
      dimensions: dims,
      description: "Convenience, Parks, Greenery",
    };
  });
}

/**
 * Return effective GROUP_BY_KEY with schools conditionally excluded.
 */
export function getEffectiveGroupByKey(kidsMode: boolean): Record<ScoreGroupKey, ScoreGroup> {
  if (kidsMode) return GROUP_BY_KEY;
  return Object.fromEntries(
    getEffectiveGroups(kidsMode).map((g) => [g.key, g]),
  ) as Record<ScoreGroupKey, ScoreGroup>;
}

// ── Data models ────────────────────────────────────────────────

export interface Scores {
  composite: number;
  deal: number | null;
  unit_amenities: number | null;
  transit: number | null;
  crime: number | null;
  noise: number | null;
  building_violations: number | null;
  parks: number | null;
  schools: number | null;
  management: number | null;
  convenience: number | null;
  shelter: number | null;
  pest: number | null;
  greenery: number | null;
  rent_stabilized: boolean;
}

export interface Trends {
  crime_direction: "improving" | "worsening" | "stable";
  crime_ratio: number;
  noise_direction: "improving" | "worsening" | "stable";
  noise_ratio: number;
}

export interface Flag {
  type: "green" | "red" | "yellow";
  text: string;
}

export interface PriceHistoryEntry {
  date: string;
  price: string;
  event: string;
}

export interface BuildingInfo {
  owner: string | null;
  year_built: number | null;
  total_units: number | null;
  stories: number | null;
  open_violations: number;
  total_violations: number;
  hpd_complaints_12mo: number;
}

// ── New listing-detail models ───────────────────────────────────────

export interface TransitStation {
  name: string;
  distance_m: number;
  routes: string[];
}

export interface CategorizedAmenities {
  services: string[];
  wellness: string[];
  outdoor: string[];
  convenience: string[];
  unit_features: string[];
}

export interface NeighborhoodInfo {
  description: string | null;
  median_rent_1br: number | null;
  median_rent_2br: number | null;
}

export interface POI {
  name: string;
  category: string;
  distance_m: number;
}

export interface ComparableListing {
  id: string;
  address: string;
  unit: string | null;
  neighborhood: string;
  price: number;
  beds: number;
  baths: number;
  sqft: number | null;
  photo: string | null;
  composite_score: number;
  group_scores: Record<string, number>;
  better_in: string | null;
  distance_km: number | null;
}

export interface Listing {
  id: string;
  address: string;
  unit: string | null;
  neighborhood: string;
  borough: string;
  price: number;
  beds: number;
  baths: number;
  sqft: number | null;
  photos: string[];
  latitude: number;
  longitude: number;
  url: string | null;
  no_fee: boolean;
  days_on_market: number | null;
  available_at: string | null;
  description: string | null;
  amenities: string[];
  price_history: PriceHistoryEntry[];
  relist_count: number;

  scores: Scores;
  data_quality: string | null;
  score_components: Record<string, Record<string, string | number | null>>;
  trends: Trends;
  flags: Flag[];
  building: BuildingInfo;

  // New listing-detail fields
  pet_policy: string | null;
  categorized_amenities: CategorizedAmenities;
  transit_stations: TransitStation[];
  neighborhood_info: NeighborhoodInfo;
  nearby_pois: POI[];
  nearby_neighborhoods: string[];
  similar: ComparableListing[];
  also_consider: ComparableListing[];
}

export interface ListingsResponse {
  listings: Listing[];
  total: number;
  page: number;
  page_size: number;
}

// ── App state types ────────────────────────────────────────────

export type QueueTab = "explore" | "watchlist" | "shortlist";

export type ViewMode = "feed" | "scan";

export type CompareTab = "table" | "flags" | "radar";

export interface FilterState {
  beds: number[];
  minPrice: number | null;
  maxPrice: number | null;
  neighborhoods: string[];
  rentStabilized: boolean | null;
  minScore: number | null;
  minSqft: number | null;
  availableBefore: string | null;
  amenities: string[];
  minDataQuality: string | null;
}

export const DEFAULT_FILTERS: FilterState = {
  beds: [],
  minPrice: null,
  maxPrice: null,
  neighborhoods: [],
  rentStabilized: null,
  minScore: null,
  minSqft: null,
  availableBefore: null,
  amenities: [],
  minDataQuality: "limited",
};

// ── Dimension breakout config ──────────────────────────────────

export interface BreakoutItem {
  key: string;          // DB column name (e.g. "pest_hpd_count")
  label: string;        // Human label
  unit?: string;        // Optional suffix (e.g. "m", "per unit")
}

/** Which dimensions have sub-component breakouts, keyed by ScoreDimension. */
export const DIMENSION_BREAKOUT: Partial<Record<ScoreDimension, BreakoutItem[]>> = {
  deal: [
    { key: "comp_median", label: "Neighborhood median" },
    { key: "comp_set_size", label: "Comp set size" },
    { key: "price_per_sqft", label: "Price per sqft" },
    { key: "comp_sqft_median", label: "Median sqft (comps)" },
    { key: "tenure_median_months", label: "Est. tenant tenure", unit: "mo" },
    { key: "tenure_cycle_count", label: "Listing cycles" },
  ],
  pest: [
    { key: "pest_hpd_count", label: "HPD pest complaints (building)" },
    { key: "pest_rodent_count", label: "311 rodent complaints (100 m)" },
    { key: "pest_total", label: "Total pest reports" },
    { key: "pest_units", label: "Residential units" },
    { key: "pest_per_unit", label: "Pests per unit" },
  ],
  convenience: [
    { key: "convenience_grocery", label: "Grocery / convenience" },
    { key: "convenience_pharmacy", label: "Pharmacies" },
    { key: "convenience_gym", label: "Gyms / fitness" },
    { key: "convenience_laundry", label: "Laundromats" },
    { key: "convenience_dining", label: "Restaurants & cafés" },
    { key: "convenience_total", label: "Weighted total" },
  ],
  unit_amenities: [
    { key: "unit_amenities_premium", label: "Premium amenities" },
    { key: "unit_amenities_standard", label: "Standard amenities" },
    { key: "unit_amenities_total", label: "Weighted total" },
  ],
  shelter: [
    { key: "shelter_count", label: "Shelters within 800 m" },
    { key: "shelter_nearest_m", label: "Nearest shelter", unit: "m" },
    { key: "shelter_nearest_name", label: "Nearest shelter" },
    { key: "project_count", label: "NYCHA buildings within 800 m" },
    { key: "project_nearest_m", label: "Nearest project", unit: "m" },
    { key: "project_nearest_name", label: "Nearest NYCHA development" },
  ],
  crime: [
    { key: "crime_felony_count", label: "Felonies (12 mo)" },
    { key: "crime_misdemeanor_count", label: "Misdemeanors (12 mo)" },
    { key: "crime_violation_count", label: "Violations (12 mo)" },
    { key: "crime_weighted_total", label: "Weighted total" },
  ],
  noise: [
    { key: "noise_complaint_count", label: "Noise complaints" },
  ],
  building_violations: [
    { key: "building_violation_count", label: "Active DOB violations" },
    { key: "building_hpd_class_a", label: "HPD Class A violations" },
    { key: "building_hpd_class_b", label: "HPD Class B violations" },
    { key: "building_hpd_class_c", label: "HPD Class C (hazardous)" },
    { key: "building_active_permits", label: "Active DOB permits" },
    { key: "building_unitsres", label: "Residential units" },
    { key: "building_violations_per_unit", label: "Weighted violations per unit" },
  ],
  transit: [
    { key: "transit_station_count", label: "Stations within 800 m" },
    { key: "transit_routes_served", label: "Unique routes" },
    { key: "transit_nearest_m", label: "Nearest station", unit: "m" },
  ],
  parks: [
    { key: "parks_distance_m", label: "Distance to park", unit: "m" },
    { key: "parks_name", label: "Best scoring park" },
    { key: "parks_acres", label: "Park size", unit: "acres" },
  ],
  management: [
    { key: "mgmt_owner", label: "Owner / management" },
    { key: "mgmt_owner_buildings", label: "Portfolio (buildings)" },
    { key: "mgmt_owner_units", label: "Portfolio (units)" },
    { key: "mgmt_complaints", label: "HPD complaints (12 mo)" },
    { key: "mgmt_hpd_heat", label: "Heat / hot water" },
    { key: "mgmt_hpd_plumbing", label: "Plumbing" },
    { key: "mgmt_hpd_paint", label: "Paint / plaster" },
    { key: "mgmt_hpd_safety", label: "Safety" },
    { key: "mgmt_heat_complaints", label: "311 heat (area)" },
    { key: "mgmt_litigations", label: "HPD litigations (open)" },
    { key: "mgmt_evictions", label: "Eviction filings (building)" },
    { key: "mgmt_complaints_per_unit", label: "Complaints per unit" },
  ],
  greenery: [
    { key: "greenery_tree_count", label: "Street trees (200 m)" },
    { key: "greenery_canopy_score", label: "Canopy score" },
    { key: "greenery_garden_count", label: "Community gardens (500 m)" },
  ],
};
