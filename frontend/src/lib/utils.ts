/**
 * Utility functions used across components.
 */

import type { ScoreDimension, ScoreGroupKey, Scores } from "./types";
import { GROUP_BY_KEY, getEffectiveGroupByKey, DIMENSION_TIER_LABELS } from "./types";

// ── Score color coding ─────────────────────────────────────────

export function scoreColor(score: number): string {
  if (score >= 75) return "text-green-400";
  if (score >= 50) return "text-yellow-400";
  if (score >= 25) return "text-orange-400";
  return "text-red-400";
}

export function scoreBg(score: number): string {
  if (score >= 75) return "bg-green-500";
  if (score >= 50) return "bg-yellow-500";
  if (score >= 25) return "bg-orange-500";
  return "bg-red-500";
}

export function scoreBgMuted(score: number): string {
  if (score >= 75) return "bg-green-500/20";
  if (score >= 50) return "bg-yellow-500/20";
  if (score >= 25) return "bg-orange-500/20";
  return "bg-red-500/20";
}

export function scoreRing(score: number): string {
  if (score >= 75) return "ring-green-500";
  if (score >= 50) return "ring-yellow-500";
  if (score >= 25) return "ring-orange-500";
  return "ring-red-500";
}

// ── Score labels ───────────────────────────────────────────────

/** Map a 0–100 score to a tier index: 0 = best, 4 = worst. */
function scoreTier(score: number): number {
  if (score >= 90) return 0;
  if (score >= 75) return 1;
  if (score >= 60) return 2;
  if (score >= 40) return 3;
  return 4;
}

const GENERIC_LABELS = ["Excellent", "Great", "Good", "Fair", "Poor"] as const;

/** Generic 5-tier label (Excellent / Great / Good / Fair / Poor). */
export function scoreLabel(score: number): string {
  return GENERIC_LABELS[scoreTier(score)];
}

/**
 * Dimension-specific label, e.g. "Very Safe" for crime=85.
 * Falls back to generic label if dimension is unknown.
 */
export function dimensionLabel(score: number, dim?: ScoreDimension): string {
  if (!dim) return scoreLabel(score);
  const tiers = DIMENSION_TIER_LABELS[dim];
  if (!tiers) return scoreLabel(score);
  return tiers[scoreTier(score)];
}

// ── Formatting ─────────────────────────────────────────────────

export function formatPrice(price: number): string {
  return `$${price.toLocaleString()}`;
}

export function formatBeds(beds: number): string {
  return beds === 0 ? "Studio" : `${beds}BR`;
}

export function formatBaths(baths: number): string {
  return baths === 1 ? "1 Ba" : `${baths} Ba`;
}

export function trendArrow(direction: string): string {
  if (direction === "improving") return "↓";
  if (direction === "worsening") return "↑";
  return "";
}

export function trendColor(direction: string): string {
  if (direction === "improving") return "text-green-400";
  if (direction === "worsening") return "text-red-400";
  return "text-zinc-500";
}

// ── Medal icons for compare ────────────────────────────────────

export function medalIcon(rank: number): string {
  if (rank === 1) return "🥇";
  if (rank === 2) return "🥈";
  if (rank === 3) return "🥉";
  return "";
}

// ── Abbreviations ──────────────────────────────────────────────

const NEIGHBORHOOD_ABBR: Record<string, string> = {
  "East Village": "EV",
  "West Village": "WV",
  "Upper West Side": "UWS",
  "Upper East Side": "UES",
  "Lower East Side": "LES",
  "Hell's Kitchen": "HK",
  "Greenwich Village": "GV",
  "Chelsea": "CH",
  "Williamsburg": "WB",
  "Bushwick": "BW",
  "Park Slope": "PS",
  "Crown Heights": "CH",
  "Bed-Stuy": "BS",
  "Astoria": "AS",
  "Long Island City": "LIC",
  "Soho": "SoHo",
  "Tribeca": "TriBeCa",
  "FiDi": "FiDi",
  "Midtown": "MT",
  "Harlem": "HAR",
};

export function neighborhoodAbbr(name: string): string {
  return NEIGHBORHOOD_ABBR[name] ?? name.slice(0, 3).toUpperCase();
}

export function formatDaysOnMarket(days: number | null): string {
  if (days === null || days === undefined) return "";
  if (days === 0) return "Listed today";
  if (days === 1) return "1 day on market";
  return `${days} days on market`;
}

// ── Dimension sort key for score access ────────────────────────

export function getScore(
  scores: Record<string, number | boolean | null>,
  dim: ScoreDimension,
): number | null {
  const val = scores[dim];
  return typeof val === "number" ? val : null;
}

/** Compute the average score for a group from a Scores object.
 *  Dimensions with a null value (no data) are excluded from the average.
 *  Returns null if every dimension in the group lacks data. */
export function getGroupScore(
  scores: Scores,
  groupKey: ScoreGroupKey,
  kidsMode: boolean = false,
): number | null {
  const groupMap = getEffectiveGroupByKey(kidsMode);
  const group = groupMap[groupKey];
  if (!group) return null;
  const raw = scores as unknown as Record<string, number | boolean | null>;
  const vals = group.dimensions
    .map((d) => raw[d])
    .filter((v): v is number => typeof v === "number" && v !== null);
  if (vals.length === 0) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

// ── MTA subway route colors ───────────────────────────────────
// Official MTA brand colors mapped to route letters

const MTA_ROUTE_COLORS: Record<string, string> = {
  "1": "#EE352E", "2": "#EE352E", "3": "#EE352E",                     // Red (7th Ave)
  "4": "#00933C", "5": "#00933C", "6": "#00933C", "6X": "#00933C",   // Green (Lex)
  "7": "#B933AD", "7X": "#B933AD",                                    // Purple (Flushing)
  A: "#0039A6", C: "#0039A6", E: "#0039A6",                           // Blue (8th Ave)
  B: "#FF6319", D: "#FF6319", F: "#FF6319", FX: "#FF6319", M: "#FF6319", // Orange
  G: "#6CBE45",                                                        // Light green
  J: "#996633", Z: "#996633",                                          // Brown
  L: "#A7A9AC",                                                        // Gray
  N: "#FCCC0A", Q: "#FCCC0A", R: "#FCCC0A", W: "#FCCC0A",           // Yellow
  S: "#808183", FS: "#808183", GS: "#808183", H: "#808183",          // Dark gray (shuttles)
  SIR: "#0039A6",                                                      // SI Railway
  Ferry: "#2850AD",
};

export function mtaRouteColor(route: string): string {
  return MTA_ROUTE_COLORS[route] ?? "#808183";
}

// ── POI category icons ────────────────────────────────────────

const POI_ICONS: Record<string, string> = {
  park: "🌳",
  school: "🏫",
  garden: "🌿",
  grocery: "🛒",
  pharmacy: "💊",
  gym: "🏋️",
  laundry: "🧺",
  restaurant: "🍽️",
  museum: "🏛️",
  college: "🎓",
};

export function poiIcon(category: string): string {
  return POI_ICONS[category] ?? "📍";
}

// ── Score group icons ────────────────────────────────────────

const GROUP_ICONS: Record<string, string> = {
  value: "💰",
  access: "🚇",
  neighborhood: "🌳",
  safety: "🛡️",
  building: "🏢",
};

export function groupIcon(key: string): string {
  return GROUP_ICONS[key] ?? "📊";
}
