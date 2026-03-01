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
