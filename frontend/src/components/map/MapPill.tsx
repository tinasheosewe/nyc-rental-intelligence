/**
 * MapPill — Horizontal pill floating at bottom-center of the map panel.
 *
 * Toggles overlay panels (Filters, Compare, Settings).
 * Clicking the active button deselects it (closes the overlay).
 * Desktop only (lg+).
 */

"use client";

import { useStore } from "@/lib/store";
import clsx from "clsx";

const ITEMS = [
  {
    key: "filter" as const,
    label: "Filters",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
      </svg>
    ),
  },
  {
    key: "settings" as const,
    label: "Settings",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
] as const;

export default function MapPill() {
  const filterSheetOpen = useStore((s) => s.filterSheetOpen);
  const setFilterSheetOpen = useStore((s) => s.setFilterSheetOpen);
  const settingsOpen = useStore((s) => s.settingsOpen);
  const setSettingsOpen = useStore((s) => s.setSettingsOpen);

  const isActive = (key: string) => {
    if (key === "filter") return filterSheetOpen;
    if (key === "settings") return settingsOpen;
    return false;
  };

  const toggle = (key: string) => {
    if (key === "filter") {
      setFilterSheetOpen(!filterSheetOpen);
    } else if (key === "settings") {
      setSettingsOpen(!settingsOpen);
    }
  };

  return (
    <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-[1001]">
      <div className="flex items-center gap-1 bg-white/90 backdrop-blur-md rounded-full shadow-lg border border-[#E5E0D8] p-1">
        {ITEMS.map(({ key, label, icon }) => {
          const active = isActive(key);
          return (
            <button
              key={key}
              onClick={() => toggle(key)}
              className={clsx(
                "flex items-center gap-1.5 px-3.5 py-2 rounded-full text-sm font-medium transition-all duration-200",
                active
                  ? "bg-amber-500 text-white shadow-sm"
                  : "text-gray-500 hover:text-gray-800 hover:bg-[#F3F0EB]",
              )}
            >
              {icon}
              <span>{label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
