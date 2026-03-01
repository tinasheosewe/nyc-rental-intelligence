/**
 * Core domain types for AptHunt.
 *
 * Mirrors the API response models exactly.
 * Single source of truth for the frontend.
 */

// ── Score dimensions ───────────────────────────────────────────

export const SCORE_DIMENSIONS = [
  "deal",
  "transit",
  "flood_risk",
  "crime",
  "noise",
  "building_violations",
  "parks",
  "schools",
  "management",
  "amenity",
  "shelter",
  "pest",
  "greenery",
] as const;

export type ScoreDimension = (typeof SCORE_DIMENSIONS)[number];

export const DIMENSION_LABELS: Record<ScoreDimension, string> = {
  deal: "Deal",
  transit: "Transit",
  flood_risk: "Flood Risk",
  crime: "Crime",
  noise: "Noise",
  building_violations: "Building Violations",
  parks: "Parks",
  schools: "Schools",
  management: "Management",
  amenity: "Amenities",
  shelter: "Shelters & Projects",
  pest: "Pests",
  greenery: "Greenery",
};

// ── Data models ────────────────────────────────────────────────

export interface Scores {
  composite: number;
  deal: number;
  transit: number;
  flood_risk: number;
  crime: number;
  noise: number;
  building_violations: number;
  parks: number;
  schools: number;
  management: number;
  amenity: number;
  shelter: number;
  pest: number;
  greenery: number;
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

export interface BuildingInfo {
  owner: string | null;
  year_built: number | null;
  total_units: number | null;
  open_violations: number;
  total_violations: number;
  hpd_complaints_12mo: number;
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

  scores: Scores;
  score_components: Record<string, Record<string, string | number | null>>;
  trends: Trends;
  flags: Flag[];
  building: BuildingInfo;
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
}

export const DEFAULT_FILTERS: FilterState = {
  beds: [],
  minPrice: null,
  maxPrice: null,
  neighborhoods: [],
  rentStabilized: null,
  minScore: null,
};

// ── Dimension breakout config ──────────────────────────────────

export interface BreakoutItem {
  key: string;          // DB column name (e.g. "pest_hpd_count")
  label: string;        // Human label
  unit?: string;        // Optional suffix (e.g. "m", "per unit")
}

/** Which dimensions have sub-component breakouts, keyed by ScoreDimension. */
export const DIMENSION_BREAKOUT: Partial<Record<ScoreDimension, BreakoutItem[]>> = {
  pest: [
    { key: "pest_hpd_count", label: "HPD pest complaints (building)" },
    { key: "pest_rodent_count", label: "311 rodent complaints (100 m)" },
    { key: "pest_total", label: "Total pest reports" },
    { key: "pest_units", label: "Residential units" },
    { key: "pest_per_unit", label: "Pests per unit" },
  ],
  amenity: [
    { key: "amenity_grocery", label: "Grocery / convenience" },
    { key: "amenity_pharmacy", label: "Pharmacies" },
    { key: "amenity_gym", label: "Gyms / fitness" },
    { key: "amenity_laundry", label: "Laundromats" },
    { key: "amenity_dining", label: "Restaurants & cafés" },
    { key: "amenity_total", label: "Weighted total" },
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
    { key: "mgmt_evictions", label: "Eviction filings (500 m)" },
    { key: "mgmt_complaints_per_unit", label: "Complaints per unit" },
  ],
  greenery: [
    { key: "greenery_tree_count", label: "Street trees (200 m)" },
    { key: "greenery_canopy_score", label: "Canopy score" },
    { key: "greenery_garden_count", label: "Community gardens (500 m)" },
    { key: "greenery_park_count", label: "Parks (500 m)" },
  ],
};
