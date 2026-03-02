/**
 * ScoreBadge — Large composite score circle display.
 *
 * Used in feed cards and listing details. Color-coded by score value.
 */

"use client";

import { scoreColor, scoreRing, scoreLabel } from "@/lib/utils";
import clsx from "clsx";

interface ScoreBadgeProps {
  score: number;
  size?: "sm" | "md" | "lg";
  label?: string;
}

const SIZES = {
  sm: "w-10 h-10 text-[9px] leading-tight",
  md: "w-14 h-14 text-[11px] leading-tight",
  lg: "w-20 h-20 text-sm leading-tight",
} as const;

export default function ScoreBadge({ score, size = "md", label }: ScoreBadgeProps) {
  return (
    <div className="flex flex-col items-center gap-1">
      <div
        className={clsx(
          "rounded-full flex items-center justify-center font-bold text-center",
          "ring-2 bg-zinc-900",
          scoreRing(score),
          scoreColor(score),
          SIZES[size],
        )}
      >
        {scoreLabel(score)}
      </div>
      {label && (
        <span className="text-[10px] text-zinc-500 uppercase tracking-wider">
          {label}
        </span>
      )}
    </div>
  );
}
