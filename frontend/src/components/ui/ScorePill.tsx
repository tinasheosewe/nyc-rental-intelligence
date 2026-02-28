/**
 * ScorePill — Compact inline score indicator.
 *
 * Shows a dimension name + score in a small rounded pill.
 * Used for the top-3 priority scores on feed cards.
 */

"use client";

import { scoreBgMuted, scoreColor } from "@/lib/utils";
import clsx from "clsx";

interface ScorePillProps {
  label: string;
  score: number;
  trend?: string;
}

export default function ScorePill({ label, score, trend }: ScorePillProps) {
  const arrow =
    trend === "improving" ? " ↓" : trend === "worsening" ? " ↑" : "";

  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium",
        scoreBgMuted(score),
        scoreColor(score),
      )}
    >
      {label} {Math.round(score)}
      {arrow}
    </span>
  );
}
