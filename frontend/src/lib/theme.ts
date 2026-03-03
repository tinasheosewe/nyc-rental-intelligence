/**
 * Reside Design System — Theme tokens & color utilities.
 *
 * Warm cream/amber palette with shifted score colors
 * that avoid clashing with the amber primary accent.
 */

// ── Score tier colors (shifted to avoid amber collision) ──────

export const SCORE_COLORS = {
  excellent: { text: "text-sky-500",    bg: "bg-sky-500",    bgMuted: "bg-sky-500/15",    ring: "ring-sky-500",    hex: "#0EA5E9" },
  good:      { text: "text-violet-500", bg: "bg-violet-500", bgMuted: "bg-violet-500/15", ring: "ring-violet-500", hex: "#8B5CF6" },
  fair:      { text: "text-orange-500", bg: "bg-orange-500", bgMuted: "bg-orange-500/15", ring: "ring-orange-500", hex: "#F97316" },
  poor:      { text: "text-red-500",    bg: "bg-red-500",    bgMuted: "bg-red-500/15",    ring: "ring-red-500",    hex: "#EF4444" },
} as const;

function getTier(score: number) {
  if (score >= 75) return SCORE_COLORS.excellent;
  if (score >= 50) return SCORE_COLORS.good;
  if (score >= 25) return SCORE_COLORS.fair;
  return SCORE_COLORS.poor;
}

export function scoreColor(score: number): string  { return getTier(score).text; }
export function scoreBg(score: number): string     { return getTier(score).bg; }
export function scoreBgMuted(score: number): string { return getTier(score).bgMuted; }
export function scoreRing(score: number): string   { return getTier(score).ring; }
export function scoreHex(score: number): string    { return getTier(score).hex; }

// ── Trend colors ──────────────────────────────────────────────

export function trendColor(direction: string): string {
  if (direction === "improving") return "text-emerald-600";
  if (direction === "worsening") return "text-red-500";
  return "text-gray-400";
}

// ── Flag colors (adjusted for cream bg) ───────────────────────

export const FLAG_STYLES: Record<string, string> = {
  green:  "text-emerald-600",
  red:    "text-red-500",
  yellow: "text-amber-600",
};

// ── Compare chart colors ──────────────────────────────────────

export const CHART_COLORS = ["#0EA5E9", "#8B5CF6", "#F97316", "#EF4444", "#06B6D4"];

// ── Pin colors for map ────────────────────────────────────────

export function pinColorHex(score: number): string {
  return scoreHex(score);
}
