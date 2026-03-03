/**
 * MapOverlayButtons — Vertical column of overlay toggle buttons on the map.
 *
 * Positioned below the Leaflet zoom controls (top-right).
 * Each button toggles a score-dimension heatmap area overlay.
 * Active button gets amber highlight; clicking again clears the overlay.
 */

"use client";

import { useState } from "react";
import { useStore } from "@/lib/store";
import type { ScoreDimension } from "@/lib/types";
import clsx from "clsx";

interface OverlayOption {
  key: ScoreDimension;
  label: string;
  description: string;
  icon: React.ReactNode;
}

const OVERLAYS: OverlayOption[] = [
  {
    key: "crime",
    label: "Crime",
    description: "Safety from violent & property crime",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    key: "noise",
    label: "Noise",
    description: "Ambient noise level from 311 complaints",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707A1 1 0 0112 5v14a1 1 0 01-1.707.707L5.586 15z" />
      </svg>
    ),
  },
  {
    key: "transit",
    label: "Transit",
    description: "Subway & bus access within walking distance",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17a2 2 0 11-4 0 2 2 0 014 0zm10 0a2 2 0 11-4 0 2 2 0 014 0z" />
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16V6a1 1 0 00-1-1H4a1 1 0 00-1 1v10m14 0V9a1 1 0 00-.667-.943l-4-1.5A1 1 0 0012 6.5V16" />
      </svg>
    ),
  },
  {
    key: "parks",
    label: "Parks",
    description: "Proximity and acreage of green spaces",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
      </svg>
    ),
  },
  {
    key: "deal",
    label: "Value",
    description: "Price relative to comparable listings",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 7h.01M7 3h5a1.99 1.99 0 011.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a4 4 0 014-4z" />
      </svg>
    ),
  },
  {
    key: "convenience",
    label: "Convenience",
    description: "Grocery, dining & everyday errands nearby",
    icon: (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
      </svg>
    ),
  },
];

export default function MapOverlayButtons() {
  const mapColorOverlay = useStore((s) => s.mapColorOverlay);
  const setMapColorOverlay = useStore((s) => s.setMapColorOverlay);
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);

  return (
    <div className="absolute top-[120px] right-[10px] z-[1001] flex flex-col gap-1">
      {OVERLAYS.map(({ key, label, description, icon }) => {
        const active = mapColorOverlay === key;
        const hovered = hoveredKey === key;
        return (
          <div key={key} className="relative">
            <button
              onClick={() => setMapColorOverlay(active ? null : key)}
              onMouseEnter={() => setHoveredKey(key)}
              onMouseLeave={() => setHoveredKey(null)}
              className={clsx(
                "w-8 h-8 flex items-center justify-center rounded-md shadow-md border transition-all duration-200",
                active
                  ? "bg-amber-500 text-white border-amber-500 shadow-amber-200"
                  : "bg-white text-gray-500 border-[#E5E0D8] hover:text-gray-800 hover:bg-[#F3F0EB]",
              )}
            >
              {icon}
            </button>

            {/* Hover tooltip — left of button */}
            {hovered && (
              <div className="absolute right-10 top-1/2 -translate-y-1/2 whitespace-nowrap bg-gray-900 text-white text-xs rounded-lg px-3 py-2 shadow-lg pointer-events-none">
                <p className="font-semibold">{label}</p>
                <p className="text-gray-300 text-[10px]">{description}</p>
                <div className="absolute right-[-4px] top-1/2 -translate-y-1/2 w-2 h-2 bg-gray-900 rotate-45" />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
