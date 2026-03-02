/**
 * Utility functions used across components.
 */

import type { ScoreDimension, ScoreGroupKey, Scores } from "./types";
import { GROUP_BY_KEY, getEffectiveGroupByKey } from "./types";

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
  scores: Record<string, number | boolean>,
  dim: ScoreDimension,
): number {
  const val = scores[dim];
  return typeof val === "number" ? val : 0;
}

/** Compute the average score for a group from a Scores object. */
export function getGroupScore(
  scores: Scores,
  groupKey: ScoreGroupKey,
  kidsMode: boolean = false,
): number {
  const groupMap = getEffectiveGroupByKey(kidsMode);
  const group = groupMap[groupKey];
  if (!group) return 0;
  const vals = group.dimensions.map((d) => (scores as unknown as Record<string, number>)[d] ?? 0);
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}
