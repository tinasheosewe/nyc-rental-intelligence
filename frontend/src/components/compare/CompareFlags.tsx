/**
 * CompareFlags — Flags tab of Compare mode.
 *
 * Each listing gets a card showing strengths and concerns,
 * contextualized relative to the comparison set.
 */

"use client";

import type { Listing, ScoreDimension } from "@/lib/types";
import { DIMENSION_LABELS, SCORE_DIMENSIONS } from "@/lib/types";
import { formatPrice, formatBeds, scoreColor, getScore } from "@/lib/utils";
import FlagList from "@/components/ui/FlagList";
import ScoreBadge from "@/components/ui/ScoreBadge";
import clsx from "clsx";

interface CompareFlagsProps {
  listings: Listing[];
  dimensions: ScoreDimension[];
}

interface RelativeFlag {
  type: "green" | "red" | "yellow";
  text: string;
}

/**
 * Generate relative flags comparing a listing against peers.
 */
function generateRelativeFlags(
  listing: Listing,
  all: Listing[],
  dims: ScoreDimension[],
): RelativeFlag[] {
  const flags: RelativeFlag[] = [];

  for (const dim of dims) {
    const myScore = getScore(listing.scores as unknown as Record<string, number | boolean>, dim);
    const allScores = all.map((l) =>
      getScore(l.scores as unknown as Record<string, number | boolean>, dim),
    );
    const avg = allScores.reduce((a, b) => a + b, 0) / allScores.length;
    const max = Math.max(...allScores);
    const min = Math.min(...allScores);
    const label = DIMENSION_LABELS[dim];

    if (myScore === max && myScore > avg + 5) {
      flags.push({
        type: "green",
        text: `Best ${label.toLowerCase()} score (${Math.round(myScore)})`,
      });
    } else if (myScore === min && myScore < avg - 5) {
      flags.push({
        type: "red",
        text: `Lowest ${label.toLowerCase()} score (${Math.round(myScore)})`,
      });
    }
  }

  // Add native flags that don't overlap with relative ones
  for (const flag of listing.flags) {
    const isDuplicate = flags.some(
      (f) => f.text.toLowerCase().includes(flag.text.toLowerCase().slice(0, 10)),
    );
    if (!isDuplicate) {
      flags.push(flag);
    }
  }

  return flags.slice(0, 6);
}

/**
 * Generate a "why it won/lost" summary sentence.
 */
function generateSummary(
  listing: Listing,
  all: Listing[],
  dims: ScoreDimension[],
): string {
  const avgComposite = all.reduce((a, l) => a + l.scores.composite, 0) / all.length;
  const diff = listing.scores.composite - avgComposite;

  const strengths: string[] = [];
  const weaknesses: string[] = [];

  for (const dim of dims) {
    const myScore = getScore(listing.scores as unknown as Record<string, number | boolean>, dim);
    const avg = all.reduce(
      (a, l) => a + getScore(l.scores as unknown as Record<string, number | boolean>, dim),
      0,
    ) / all.length;
    const delta = Math.round(myScore - avg);
    const label = DIMENSION_LABELS[dim].toLowerCase();

    if (delta > 5) strengths.push(`${label} (+${delta})`);
    else if (delta < -5) weaknesses.push(`${label} (${delta})`);
  }

  const parts: string[] = [];
  if (strengths.length > 0) parts.push(`Strongest in ${strengths.join(", ")}`);
  if (weaknesses.length > 0) parts.push(`weaker in ${weaknesses.join(", ")}`);

  return parts.join("; ") || "Scores close to group average across all dimensions";
}

export default function CompareFlags({ listings, dimensions }: CompareFlagsProps) {
  const sorted = [...listings].sort((a, b) => b.scores.composite - a.scores.composite);
  const winnerId = sorted[0]?.id;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {sorted.map((listing) => {
        const flags = generateRelativeFlags(listing, listings, dimensions);
        const summary = generateSummary(listing, listings, dimensions);
        const isWinner = listing.id === winnerId;

        return (
          <div
            key={listing.id}
            className={clsx(
              "rounded-xl border p-4 space-y-3",
              isWinner
                ? "border-green-500/50 bg-green-500/5"
                : "border-zinc-800 bg-zinc-900",
            )}
          >
            {/* Header */}
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-semibold text-white">
                    {listing.address}
                  </h3>
                  {isWinner && (
                    <span className="text-[10px] bg-green-500/20 text-green-400 px-1.5 py-0.5 rounded font-medium">
                      WINNER
                    </span>
                  )}
                </div>
                <p className="text-xs text-zinc-500 mt-0.5">
                  {formatPrice(listing.price)} · {formatBeds(listing.beds)} ·{" "}
                  {listing.neighborhood}
                </p>
              </div>
              <ScoreBadge score={listing.scores.composite} size="sm" />
            </div>

            {/* Flags */}
            <FlagList
              flags={flags.map((f) => ({ type: f.type, text: f.text }))}
            />

            {/* Summary */}
            <p className="text-xs text-zinc-500 italic border-t border-zinc-800 pt-2">
              {summary}
            </p>
          </div>
        );
      })}
    </div>
  );
}
