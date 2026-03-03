/**
 * ScoreRings — SVG donut-ring score visualization.
 *
 * Shows a large composite ring in the center with smaller
 * group rings arranged around it. Used in the detail panel.
 */

"use client";

import { scoreHex } from "@/lib/theme";
import { scoreLabel, getGroupScore } from "@/lib/utils";
import type { Scores, ScoreGroupKey } from "@/lib/types";
import { getEffectiveGroups } from "@/lib/types";

interface ScoreRingsProps {
  scores: Scores;
  kidsMode?: boolean;
  priorities: ScoreGroupKey[];
}

function Ring({
  score,
  size,
  strokeWidth,
  label,
  icon,
}: {
  score: number;
  size: number;
  strokeWidth: number;
  label?: string;
  icon?: string;
}) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score / 100) * circumference;
  const color = scoreHex(score);

  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          {/* Background track */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="#E5E0D8"
            strokeWidth={strokeWidth}
          />
          {/* Score arc */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={strokeWidth}
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            strokeLinecap="round"
            className="transition-all duration-700 ease-out"
          />
        </svg>
        {/* Center text */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          {icon && <span className="text-sm">{icon}</span>}
          <span className="text-xs font-bold" style={{ color }}>
            {scoreLabel(score)}
          </span>
        </div>
      </div>
      {label && (
        <span className="text-[10px] text-gray-500 font-medium text-center leading-tight">
          {label}
        </span>
      )}
    </div>
  );
}

export default function ScoreRings({ scores, kidsMode = false, priorities }: ScoreRingsProps) {
  const effectiveGroups = getEffectiveGroups(kidsMode);
  const groupMap = Object.fromEntries(effectiveGroups.map((g) => [g.key, g]));
  const orderedGroups = priorities.map((gk) => groupMap[gk]).filter(Boolean);

  return (
    <div className="flex flex-col items-center gap-4">
      {/* Main composite ring */}
      <Ring score={scores.composite} size={88} strokeWidth={6} label="Composite" />

      {/* Group rings */}
      <div className="flex items-start justify-center gap-3 flex-wrap">
        {orderedGroups.map((group) => {
          const gs = getGroupScore(scores, group.key, kidsMode);
          if (gs === null) return null;
          return (
            <Ring
              key={group.key}
              score={gs}
              size={56}
              strokeWidth={4}
              label={group.label}
              icon={group.icon}
            />
          );
        })}
      </div>
    </div>
  );
}
