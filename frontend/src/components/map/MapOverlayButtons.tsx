/**
 * MapOverlayButtons — Vertical column of overlay toggle buttons on the map.
 *
 * Positioned below the Leaflet zoom controls (top-right).
 * Each button toggles a score-dimension "heatmap" coloring on the pins.
 * Active button gets amber highlight; clicking again returns to composite.
 */

"use client";

import { useStore } from "@/lib/store";
import type { ScoreDimension } from "@/lib/types";
import clsx from "clsx";

interface OverlayOption {
  key: ScoreDimension;
  label: string;
  icon: React.ReactNode;
}

const OVERLAYS: OverlayOption[] = [
  {
    key: "crime",
    label: "Crime",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
      </svg>
    ),
  },
  {
    key: "noise",
    label: "Noise",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.536 8.464a5 5 0 010 7.072M17.95 6.05a8 8 0 010 11.9M6.5 8.788l4.5-3.788v14l-4.5-3.788H3a1 1 0 01-1-1v-4.424a1 1 0 011-1h3.5z" />
      </svg>
    ),
  },
  {
    key: "transit",
    label: "Transit",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 17l-2 2m0 0l-2-2m2 2V3m8 14l2 2m0 0l2-2m-2 2V3M3 7h4m-4 4h4m10-4h4m-4 4h4" />
      </svg>
    ),
  },
  {
    key: "parks",
    label: "Parks",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
      </svg>
    ),
  },
  {
    key: "deal",
    label: "Value",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    key: "convenience",
    label: "Convenience",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 100 4 2 2 0 000-4z" />
      </svg>
    ),
  },
];

export default function MapOverlayButtons() {
  const mapColorOverlay = useStore((s) => s.mapColorOverlay);
  const setMapColorOverlay = useStore((s) => s.setMapColorOverlay);

  return (
    <div className="absolute top-[120px] right-[10px] z-[1001] flex flex-col gap-1">
      {OVERLAYS.map(({ key, label, icon }) => {
        const active = mapColorOverlay === key;
        return (
          <button
            key={key}
            onClick={() => setMapColorOverlay(active ? null : key)}
            className={clsx(
              "w-8 h-8 flex items-center justify-center rounded-md shadow-md border transition-all duration-200",
              active
                ? "bg-amber-500 text-white border-amber-500 shadow-amber-200"
                : "bg-white text-gray-500 border-[#E5E0D8] hover:text-gray-800 hover:bg-[#F3F0EB]",
            )}
            title={active ? `Hide ${label} overlay` : `Show ${label} overlay`}
          >
            {icon}
          </button>
        );
      })}
    </div>
  );
}
