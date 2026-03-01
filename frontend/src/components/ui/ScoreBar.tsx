/**
 * ScoreBar — Horizontal score bar with label and value.
 *
 * Used in score breakdowns and compare table cells.
 * Optionally expandable to show sub-component breakout details.
 */

"use client";

import { useState } from "react";
import { scoreBg, trendArrow, trendColor } from "@/lib/utils";
import type { BreakoutItem } from "@/lib/types";
import clsx from "clsx";

interface ScoreBarProps {
  label: string;
  score: number;
  maxScore?: number;
  trend?: string;
  medal?: string;
  compact?: boolean;
  /** Sub-component breakout items to reveal on tap. */
  breakout?: BreakoutItem[];
  /** Raw component values for this dimension, keyed by DB column name. */
  componentValues?: Record<string, string | number | null>;
}

export default function ScoreBar({
  label,
  score,
  maxScore = 100,
  trend,
  medal,
  compact = false,
  breakout,
  componentValues,
}: ScoreBarProps) {
  const [expanded, setExpanded] = useState(false);
  const pct = Math.min((score / maxScore) * 100, 100);
  const hasBreakout = breakout && breakout.length > 0 && componentValues;

  return (
    <div>
      <div
        className={clsx(
          "flex items-center gap-3",
          compact ? "gap-2" : "gap-3",
          hasBreakout && "cursor-pointer",
        )}
        onClick={hasBreakout ? () => setExpanded((v) => !v) : undefined}
      >
        <span
          className={clsx(
            "text-zinc-400 shrink-0",
            compact ? "w-16 text-xs" : "w-28 text-sm",
          )}
        >
          {label}
        </span>
        <div className="flex-1 h-2 bg-zinc-800 rounded-full overflow-hidden">
          <div
            className={clsx("h-full rounded-full transition-all duration-500", scoreBg(score))}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className={clsx("font-mono shrink-0", compact ? "text-xs w-8" : "text-sm w-10")}>
          {Math.round(score)}
        </span>
        {trend && (
          <span className={clsx("text-sm shrink-0", trendColor(trend))}>
            {trendArrow(trend)}
          </span>
        )}
        {medal && <span className="text-sm shrink-0">{medal}</span>}
        {hasBreakout && (
          <span className="text-xs text-zinc-600 shrink-0 w-4 text-center select-none">
            {expanded ? "▾" : "▸"}
          </span>
        )}
      </div>

      {/* Breakout detail panel */}
      {expanded && hasBreakout && (
        <div className="ml-[calc(7rem+12px)] mt-1.5 mb-1 space-y-1 border-l-2 border-zinc-800 pl-3">
          {breakout!.map(({ key, label: bLabel, unit }) => {
            const val = componentValues![key];
            if (val == null) return null;
            const display =
              typeof val === "number"
                ? Number.isInteger(val)
                  ? val.toLocaleString()
                  : val.toFixed(2)
                : String(val);
            return (
              <div key={key} className="flex items-center justify-between text-xs">
                <span className="text-zinc-500">{bLabel}</span>
                <span className="text-zinc-300 font-mono">
                  {display}
                  {unit ? ` ${unit}` : ""}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
