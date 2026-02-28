/**
 * SortChips — Horizontal scrollable sort dimension pills.
 *
 * Below the top bar. Tap to re-sort the active queue.
 */

"use client";

import { useStore } from "@/lib/store";
import { SCORE_DIMENSIONS, DIMENSION_LABELS } from "@/lib/types";
import clsx from "clsx";

const SORT_OPTIONS = [
  { key: "composite", label: "Composite" },
  ...SCORE_DIMENSIONS.map((d) => ({
    key: d,
    label: DIMENSION_LABELS[d],
  })),
];

export default function SortChips() {
  const sortBy = useStore((s) => s.sortBy);
  const setSortBy = useStore((s) => s.setSortBy);

  return (
    <div className="sticky top-14 z-40 bg-zinc-950/80 backdrop-blur-sm border-b border-zinc-800/50">
      <div className="flex items-center gap-2 px-4 py-2 max-w-7xl mx-auto overflow-x-auto scrollbar-hide">
        {SORT_OPTIONS.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setSortBy(key)}
            className={clsx(
              "shrink-0 px-3 py-1 rounded-full text-xs font-medium transition-all duration-200",
              sortBy === key
                ? "bg-white text-zinc-900 shadow-sm"
                : "bg-zinc-800/50 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200",
            )}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
