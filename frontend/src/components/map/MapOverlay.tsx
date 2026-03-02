/**
 * MapOverlay — Full-canvas map with context-aware listing pins.
 *
 * Uses Leaflet (dynamic import to avoid SSR issues).
 *
 * Context-aware highlighting:
 *   - Explore / Scan:  all loaded listings, normal pins
 *   - Explore / Feed:  current card pin highlighted, others dimmed
 *   - Watchlist tab:   watchlist pins, normal
 *   - Shortlist tab:   shortlist pins, normal
 *   - Compare open:    compared pins highlighted, others dimmed
 */

"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useStore } from "@/lib/store";
import type { Listing } from "@/lib/types";

// Dynamically import MapContainer + MapContent as a single client-only bundle
const MapContainer = dynamic(
  () => import("react-leaflet").then((m) => m.MapContainer),
  { ssr: false },
);
const MapContent = dynamic(() => import("./MapContent"), { ssr: false });

const NYC_CENTER: [number, number] = [40.73, -73.99];
const DEFAULT_ZOOM = 12;

export default function MapOverlay() {
  const mapOpen = useStore((s) => s.mapOpen);
  const setMapOpen = useStore((s) => s.setMapOpen);
  const activeTab = useStore((s) => s.activeTab);
  const viewMode = useStore((s) => s.viewMode);
  const feedIndex = useStore((s) => s.feedIndex);
  const listings = useStore((s) => s.listings);
  const watchlist = useStore((s) => s.watchlist);
  const shortlist = useStore((s) => s.shortlist);
  const skipped = useStore((s) => s.skipped);
  const compareIds = useStore((s) => s.compareIds);
  const compareOpen = useStore((s) => s.compareOpen);
  const addToWatchlist = useStore((s) => s.addToWatchlist);
  const addToShortlist = useStore((s) => s.addToShortlist);

  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  // ── Derive which listings to display (filtering out skipped) ──
  const displayListings = useMemo((): Listing[] => {
    switch (activeTab) {
      case "watchlist":
        return watchlist;
      case "shortlist":
        return shortlist;
      default:
        return listings.filter((l) => !skipped.has(l.id));
    }
  }, [activeTab, listings, watchlist, shortlist, skipped]);

  // ── Derive focused listing ID (feed mode single-card) ────────
  const focusedId = useMemo((): string | null => {
    if (activeTab !== "explore" || viewMode !== "feed") return null;
    const visible = listings.filter((l) => !skipped.has(l.id));
    const idx = Math.min(feedIndex, visible.length - 1);
    return visible[idx]?.id ?? null;
  }, [activeTab, viewMode, feedIndex, listings, skipped]);

  // ── Derive highlight set ─────────────────────────────────────
  const highlightIds = useMemo((): Set<string> => {
    if (compareOpen && compareIds.size > 0) return compareIds;
    if (focusedId) return new Set([focusedId]);
    return new Set();
  }, [compareOpen, compareIds, focusedId]);

  if (!mapOpen || !mounted) return null;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-zinc-950">
      {/* Close button */}
      <button
        onClick={() => setMapOpen(false)}
        className="absolute top-4 left-4 z-[1000] p-2 rounded-lg bg-zinc-900/80 backdrop-blur text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors shadow-lg"
        title="Close map"
      >
        <svg
          className="w-5 h-5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M6 18L18 6M6 6l12 12"
          />
        </svg>
      </button>

      {/* Context badge */}
      {highlightIds.size > 0 && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 z-[1000] px-3 py-1.5 rounded-lg bg-zinc-900/80 backdrop-blur text-xs text-zinc-300 shadow-lg">
          {compareOpen
            ? `Comparing ${highlightIds.size} listings`
            : "Viewing listing"}
        </div>
      )}

      {/* Map */}
      <div className="flex-1 w-full">
        <MapContainer
          center={NYC_CENTER}
          zoom={DEFAULT_ZOOM}
          className="w-full h-full"
          style={{ background: "#18181b" }}
        >
          <MapContent
            listings={displayListings}
            highlightIds={highlightIds}
            focusedId={focusedId}
            addToWatchlist={addToWatchlist}
            addToShortlist={addToShortlist}
          />
        </MapContainer>
      </div>
    </div>
  );
}
