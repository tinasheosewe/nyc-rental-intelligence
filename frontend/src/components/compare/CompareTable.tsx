/**
 * CompareTable — Table tab of Compare mode.
 *
 * Columns = listings, rows = selected score groups.
 * Shows score bars, values, and medal icons for top 3.
 */

"use client";

import type { Listing, ScoreGroupKey } from "@/lib/types";
import { GROUP_BY_KEY } from "@/lib/types";
import { scoreBg, scoreColor, medalIcon, formatPrice, formatBeds, getGroupScore, scoreLabel } from "@/lib/utils";
import { useStore } from "@/lib/store";
import clsx from "clsx";

interface CompareTableProps {
  listings: Listing[];
  groups: ScoreGroupKey[];
}

export default function CompareTable({ listings, groups }: CompareTableProps) {
  const kidsMode = useStore((s) => s.kidsMode);
  // Compute ranks per group
  const ranks: Record<string, Record<string, number>> = {};
  for (const gk of groups) {
    const sorted = [...listings].sort(
      (a, b) => getGroupScore(b.scores, gk, kidsMode) - getGroupScore(a.scores, gk, kidsMode),
    );
    ranks[gk] = {};
    sorted.forEach((l, i) => {
      ranks[gk][l.id] = i + 1;
    });
  }

  // Composite ranks
  const compositeSorted = [...listings].sort(
    (a, b) => b.scores.composite - a.scores.composite,
  );
  const compositeRanks: Record<string, number> = {};
  compositeSorted.forEach((l, i) => {
    compositeRanks[l.id] = i + 1;
  });

  const winnerId = compositeSorted[0]?.id;

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[500px]">
        <thead>
          <tr className="border-b border-zinc-800">
            <th className="text-left text-xs text-zinc-500 py-3 px-2 w-28" />
            {listings.map((l) => (
              <th key={l.id} className="text-center py-3 px-2">
                <div className="flex flex-col items-center gap-1">
                  <div className="w-10 h-10 rounded-lg bg-zinc-800 overflow-hidden">
                    {l.photos.length > 0 ? (
                      <img src={l.photos[0]} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-zinc-600 text-xs">📸</div>
                    )}
                  </div>
                  <span className="text-xs text-zinc-300 truncate max-w-[120px]">
                    {l.address}
                  </span>
                  <span className="text-[10px] text-zinc-500">
                    {formatPrice(l.price)} · {formatBeds(l.beds)}
                  </span>
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {groups.map((gk) => {
            const g = GROUP_BY_KEY[gk];
            return (
              <tr key={gk} className="border-b border-zinc-800/50">
                <td className="text-xs text-zinc-400 py-2.5 px-2">
                  {g.icon} {g.label}
                </td>
                {listings.map((l) => {
                  const score = getGroupScore(l.scores, gk, kidsMode);
                  const rank = ranks[gk][l.id];
                  return (
                    <td key={l.id} className="text-center py-2.5 px-2">
                      <div className="flex items-center justify-center gap-2">
                        <div className="w-16 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                          <div
                            className={clsx("h-full rounded-full", scoreBg(score))}
                            style={{ width: `${score}%` }}
                          />
                        </div>
                        <span className={clsx("text-xs", scoreColor(score))}>
                          {scoreLabel(score)}
                        </span>
                        <span className="text-xs">{medalIcon(rank)}</span>
                      </div>
                    </td>
                  );
                })}
              </tr>
            );
          })}
          {/* Composite row */}
          <tr className="border-t-2 border-zinc-700">
            <td className="text-xs font-semibold text-zinc-300 py-3 px-2 uppercase">
              Composite
            </td>
            {listings.map((l) => (
              <td key={l.id} className="text-center py-3 px-2">
                <div className="flex items-center justify-center gap-2">
                  <span
                    className={clsx(
                      "text-sm font-bold",
                      scoreColor(l.scores.composite),
                    )}
                  >
                    {scoreLabel(l.scores.composite)}
                  </span>
                  <span className="text-xs">{medalIcon(compositeRanks[l.id])}</span>
                  {l.id === winnerId && (
                    <span className="text-[10px] bg-green-500/20 text-green-400 px-1.5 py-0.5 rounded ml-1">
                      WINNER
                    </span>
                  )}
                </div>
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}
