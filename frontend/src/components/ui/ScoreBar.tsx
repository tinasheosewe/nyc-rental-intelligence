/**
 * ScoreBar — Horizontal score bar with label and value.
 *
 * Used in score breakdowns and compare table cells.
 */

"use client";

import { scoreBg, trendArrow, trendColor } from "@/lib/utils";
import clsx from "clsx";

interface ScoreBarProps {
  label: string;
  score: number;
  maxScore?: number;
  trend?: string;
  medal?: string;
  compact?: boolean;
}

export default function ScoreBar({
  label,
  score,
  maxScore = 100,
  trend,
  medal,
  compact = false,
}: ScoreBarProps) {
  const pct = Math.min((score / maxScore) * 100, 100);

  return (
    <div className={clsx("flex items-center gap-3", compact ? "gap-2" : "gap-3")}>
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
    </div>
  );
}
