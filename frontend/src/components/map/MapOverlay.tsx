/**
 * MapOverlay — Full-canvas map with context-aware listing pins.
 *
 * Uses Leaflet (dynamic import to avoid SSR issues).
 *
 * Context-aware highlighting:
 *   - Explore / Scan:  all loaded listings, normal pins
 *   - Explore / Feed:  current card pin highlighted, others dimmed
 *   - Watchlist / Shortlist tab:  queue pins, expanded listing highlighted
 *   - Compare open:    compared pins highlighted, others dimmed
 */

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useStore } from "@/lib/store";
import type { Listing } from "@/lib/types";

// Dynamically import MapContainer + MapContent (no SSR — Leaflet needs window)
const MapContainer = dynamic(
  () => import("react-leaflet").then((m) => m.MapContainer),
  { ssr: false },
);
const MapContent = dynamic(() => import("./MapContent"), { ssr: false });

const NYC_CENTER: [number, number] = [40.73, -73.99];
const DEFAULT_ZOOM = 12;
const FOCUSED_ZOOM = 15;

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
  const expandedQueueId = useStore((s) => s.expandedQueueId);
  const addToWatchlist = useStore((s) => s.addToWatchlist);
  const addToShortlist = useStore((s) => s.addToShortlist);
  const mapColorOverlay = useStore((s) => s.mapColorOverlay);

  const [mounted, setMounted] = useState(false);
  const [tilesReady, setTilesReady] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Reset tile-ready state when map re-opens
  useEffect(() => {
    if (mapOpen) setTilesReady(false);
  }, [mapOpen]);

  const handleTilesLoaded = useCallback(() => setTilesReady(true), []);

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

  // ── Derive focused listing ID ────────────────────────────────
  const focusedId = useMemo((): string | null => {
    // Compare mode — no single focus
    if (compareOpen && compareIds.size > 0) return null;
    // Queue tabs — expanded listing
    if (activeTab === "watchlist" || activeTab === "shortlist") {
      return expandedQueueId;
    }
    // Explore feed — current card
    if (activeTab === "explore" && viewMode === "feed") {
      const visible = listings.filter((l) => !skipped.has(l.id));
      const idx = Math.min(feedIndex, visible.length - 1);
      return visible[idx]?.id ?? null;
    }
    return null;
  }, [activeTab, viewMode, feedIndex, listings, skipped, compareOpen, compareIds, expandedQueueId]);

  // ── Derive highlight set ─────────────────────────────────────
  const highlightIds = useMemo((): Set<string> => {
    if (compareOpen && compareIds.size > 0) return compareIds;
    if (focusedId) return new Set([focusedId]);
    return new Set();
  }, [compareOpen, compareIds, focusedId]);

  // ── Compute initial center/zoom so map starts at the right place ──
  const { initialCenter, initialZoom } = useMemo(() => {
    // Single focused listing → start centred on it
    if (focusedId) {
      const listing = displayListings.find((l) => l.id === focusedId);
      if (listing) {
        return {
          initialCenter: [listing.latitude, listing.longitude] as [number, number],
          initialZoom: FOCUSED_ZOOM,
        };
      }
    }
    return { initialCenter: NYC_CENTER, initialZoom: DEFAULT_ZOOM };
  }, [focusedId, displayListings]);

  if (!mapOpen || !mounted) return null;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[#FAF7F2]">
      {/* Close button */}
      <button
        onClick={() => setMapOpen(false)}
        className="absolute top-4 left-4 z-[1000] p-2 rounded-lg bg-white/80 backdrop-blur text-gray-500 hover:text-gray-800 hover:bg-white transition-colors shadow-lg"
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
        <div className="absolute top-4 left-1/2 -translate-x-1/2 z-[1000] px-3 py-1.5 rounded-lg bg-white/90 backdrop-blur text-xs text-gray-600 shadow-lg">
          {compareOpen
            ? `Comparing ${highlightIds.size} listings`
            : "Viewing listing"}
        </div>
      )}

      {/* Loading spinner — shown until first tiles arrive */}
      {!tilesReady && (
        <div className="absolute inset-0 z-[999] flex items-center justify-center pointer-events-none">
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 border-2 border-gray-200 border-t-amber-500 rounded-full animate-spin" />
            <span className="text-xs text-gray-400">Loading map…</span>
          </div>
        </div>
      )}

      {/* Map */}
      <div className="flex-1 w-full">
        <MapContainer
          center={initialCenter}
          zoom={initialZoom}
          zoomControl={false}
          preferCanvas
          className="w-full h-full"
          style={{ background: "#F3F4F6" }}
        >
          <MapContent
            listings={displayListings}
            highlightIds={highlightIds}
            focusedId={focusedId}
            colorBy={mapColorOverlay}
            addToWatchlist={addToWatchlist}
            addToShortlist={addToShortlist}
            onTilesLoaded={handleTilesLoaded}
          />
        </MapContainer>
      </div>
    </div>
  );
}
