/**
 * FlagList — Renders green/red/yellow insight flags.
 */

"use client";

import type { Flag } from "@/lib/types";
import clsx from "clsx";

const FLAG_STYLES: Record<string, string> = {
  green: "text-green-400",
  red: "text-red-400",
  yellow: "text-yellow-400",
};

const FLAG_ICONS: Record<string, string> = {
  green: "🟢",
  red: "🔴",
  yellow: "🟡",
};

interface FlagListProps {
  flags: Flag[];
  maxItems?: number;
}

export default function FlagList({ flags, maxItems }: FlagListProps) {
  const visible = maxItems ? flags.slice(0, maxItems) : flags;

  return (
    <ul className="space-y-1.5">
      {visible.map((flag, i) => (
        <li key={i} className={clsx("flex items-start gap-2 text-sm", FLAG_STYLES[flag.type])}>
          <span className="shrink-0 text-xs mt-0.5">{FLAG_ICONS[flag.type]}</span>
          <span>{flag.text}</span>
        </li>
      ))}
    </ul>
  );
}
