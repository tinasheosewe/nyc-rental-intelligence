/**
 * SortChips — Horizontal scrollable sort pills.
 *
 * Below the top bar. Tap to re-sort the active queue.
 * Shows Composite + 5 score groups.
 */

"use client";

import { useMemo } from "react";
import { useStore } from "@/lib/store";
import { SCORE_GROUPS, getEffectiveGroups } from "@/lib/types";
import clsx from "clsx";

export default function SortChips() {
  const sortBy = useStore((s) => s.sortBy);
  const setSortBy = useStore((s) => s.setSortBy);
  const kidsMode = useStore((s) => s.kidsMode);

  const sortOptions = useMemo(
    () => [
      { key: "composite", label: "Composite" },
      ...getEffectiveGroups(kidsMode).map((g) => ({
        key: g.key,
        label: `${g.icon} ${g.label}`,
      })),
    ],
    [kidsMode],
  );

  return (
    <div className="sticky top-14 z-40 bg-zinc-950/80 backdrop-blur-sm border-b border-zinc-800/50">
      <div className="flex items-center gap-2 px-4 py-2 max-w-7xl mx-auto overflow-x-auto scrollbar-hide">
        {sortOptions.map(({ key, label }) => (
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
