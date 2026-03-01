/**
 * MapOverlay — Full-canvas map with listing pins.
 *
 * Uses Leaflet (dynamic import to avoid SSR issues).
 * Shows listings from the active queue as color-coded pins.
 * Layer toggle chips for heatmap overlays.
 */

"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useStore } from "@/lib/store";
import type { Listing } from "@/lib/types";
import { scoreColor, formatPrice, formatBeds } from "@/lib/utils";
import clsx from "clsx";

// Dynamically import Leaflet components (no SSR)
const MapContainer = dynamic(
  () => import("react-leaflet").then((m) => m.MapContainer),
  { ssr: false },
);
const TileLayer = dynamic(
  () => import("react-leaflet").then((m) => m.TileLayer),
  { ssr: false },
);
const CircleMarker = dynamic(
  () => import("react-leaflet").then((m) => m.CircleMarker),
  { ssr: false },
);
const Popup = dynamic(
  () => import("react-leaflet").then((m) => m.Popup),
  { ssr: false },
);

const NYC_CENTER: [number, number] = [40.73, -73.99];
const DEFAULT_ZOOM = 12;

const OVERLAY_CHIPS = [
  { key: "crime", label: "Crime" },
  { key: "noise", label: "Noise" },
  { key: "flood", label: "Flood" },
  { key: "transit", label: "Transit" },
  { key: "amenities", label: "Amenities" },
] as const;

function pinColor(score: number): string {
  if (score >= 75) return "#22c55e";
  if (score >= 50) return "#eab308";
  if (score >= 25) return "#f97316";
  return "#ef4444";
}

export default function MapOverlay() {
  const mapOpen = useStore((s) => s.mapOpen);
  const setMapOpen = useStore((s) => s.setMapOpen);
  const activeTab = useStore((s) => s.activeTab);
  const listings = useStore((s) => s.listings);
  const watchlist = useStore((s) => s.watchlist);
  const shortlist = useStore((s) => s.shortlist);
  const addToWatchlist = useStore((s) => s.addToWatchlist);
  const addToShortlist = useStore((s) => s.addToShortlist);

  const [activeOverlays, setActiveOverlays] = useState<Set<string>>(new Set());
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const toggleOverlay = (key: string) => {
    setActiveOverlays((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // Show listings from active queue
  const displayListings = useMemo((): Listing[] => {
    switch (activeTab) {
      case "watchlist":
        return watchlist;
      case "shortlist":
        return shortlist;
      default:
        return listings;
    }
  }, [activeTab, listings, watchlist, shortlist]);

  if (!mapOpen || !mounted) return null;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-zinc-950">
      {/* Close button */}
      <button
        onClick={() => setMapOpen(false)}
        className="absolute top-4 left-4 z-[1000] p-2 rounded-lg bg-zinc-900/80 backdrop-blur text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors shadow-lg"
        title="Close map"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>

      {/* Overlay chips */}
      <div className="absolute top-20 right-4 z-[1000] flex flex-col gap-1">
        {OVERLAY_CHIPS.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => toggleOverlay(key)}
            className={clsx(
              "px-3 py-1.5 rounded-lg text-xs font-medium backdrop-blur transition-all shadow-lg",
              activeOverlays.has(key)
                ? "bg-white text-zinc-900"
                : "bg-zinc-900/80 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200",
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Map */}
      <div className="flex-1 w-full">
        <MapContainer
          center={NYC_CENTER}
          zoom={DEFAULT_ZOOM}
          className="w-full h-full"
          style={{ background: "#18181b" }}
        >
          <TileLayer
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
            attribution='&copy; <a href="https://carto.com/">CARTO</a>'
          />
          {displayListings.map((listing) => (
            <CircleMarker
              key={listing.id}
              center={[listing.latitude, listing.longitude]}
              radius={8}
              pathOptions={{
                color: pinColor(listing.scores.composite),
                fillColor: pinColor(listing.scores.composite),
                fillOpacity: 0.8,
                weight: 2,
              }}
            >
              <Popup>
                <div className="text-xs space-y-1 min-w-[160px]">
                  <p className="font-semibold text-zinc-900">{listing.address}</p>
                  <p className="text-zinc-600">
                    {formatPrice(listing.price)} · {formatBeds(listing.beds)}
                  </p>
                  <p className="text-zinc-600">
                    Score: {Math.round(listing.scores.composite)}
                  </p>
                  <div className="flex gap-1 pt-1">
                    <button
                      onClick={() => addToWatchlist(listing)}
                      className="text-[10px] px-2 py-0.5 bg-zinc-100 rounded hover:bg-zinc-200"
                    >
                      + Watchlist
                    </button>
                    <button
                      onClick={() => addToShortlist(listing)}
                      className="text-[10px] px-2 py-0.5 bg-zinc-100 rounded hover:bg-zinc-200"
                    >
                      ★ Shortlist
                    </button>
                  </div>
                </div>
              </Popup>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>
    </div>
  );
}
