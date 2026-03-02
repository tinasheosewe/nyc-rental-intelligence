/**
 * MapContent — Inner map component rendered inside MapContainer.
 *
 * Split from MapOverlay so useMap() works (must be inside MapContainer).
 * Handles: tile layer, pins, highlighting, fly-to, fit-bounds.
 */

"use client";

import { useEffect, useRef } from "react";
import {
  TileLayer,
  CircleMarker,
  Popup,
  Tooltip,
  useMap,
} from "react-leaflet";
import type { Listing } from "@/lib/types";
import { formatPrice, formatBeds } from "@/lib/utils";

interface MapContentProps {
  listings: Listing[];
  highlightIds: Set<string>;
  focusedId: string | null;
  addToWatchlist: (l: Listing) => void;
  addToShortlist: (l: Listing) => void;
}

function pinColor(score: number): string {
  if (score >= 75) return "#22c55e";
  if (score >= 50) return "#eab308";
  if (score >= 25) return "#f97316";
  return "#ef4444";
}

export default function MapContent({
  listings,
  highlightIds,
  focusedId,
  addToWatchlist,
  addToShortlist,
}: MapContentProps) {
  const map = useMap();
  const prevFocusedId = useRef<string | null>(null);

  const hasHighlights = highlightIds.size > 0;

  // Fly to focused listing when it changes
  useEffect(() => {
    if (!focusedId || focusedId === prevFocusedId.current) return;
    prevFocusedId.current = focusedId;

    const listing = listings.find((l) => l.id === focusedId);
    if (listing) {
      map.flyTo([listing.latitude, listing.longitude], 15, {
        duration: 0.8,
      });
    }
  }, [focusedId, listings, map]);

  // Fit bounds to all highlighted listings (compare mode)
  useEffect(() => {
    if (highlightIds.size < 2 || focusedId) return;

    const highlighted = listings.filter((l) => highlightIds.has(l.id));
    if (highlighted.length < 2) return;

    const L = require("leaflet");
    const bounds = L.latLngBounds(
      highlighted.map((l) => [l.latitude, l.longitude]),
    );
    map.fitBounds(bounds, { padding: [60, 60], maxZoom: 15 });
  }, [highlightIds, listings, map, focusedId]);

  return (
    <>
      <TileLayer
        url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        attribution='&copy; <a href="https://carto.com/">CARTO</a>'
      />

      {/* Render non-highlighted pins first (below), then highlighted (above) */}
      {listings
        .filter((l) => !highlightIds.has(l.id))
        .map((listing) => (
          <CircleMarker
            key={listing.id}
            center={[listing.latitude, listing.longitude]}
            radius={hasHighlights ? 5 : 8}
            pathOptions={{
              color: pinColor(listing.scores.composite),
              fillColor: pinColor(listing.scores.composite),
              fillOpacity: hasHighlights ? 0.2 : 0.8,
              weight: hasHighlights ? 1 : 2,
            }}
          >
            <Popup>
              <PinPopup
                listing={listing}
                addToWatchlist={addToWatchlist}
                addToShortlist={addToShortlist}
              />
            </Popup>
          </CircleMarker>
        ))}

      {/* Highlighted pins — rendered last so they stack on top */}
      {listings
        .filter((l) => highlightIds.has(l.id))
        .map((listing) => (
          <CircleMarker
            key={`hl-${listing.id}`}
            center={[listing.latitude, listing.longitude]}
            radius={12}
            pathOptions={{
              color: "#ffffff",
              fillColor: pinColor(listing.scores.composite),
              fillOpacity: 1,
              weight: 3,
            }}
          >
            <Tooltip
              direction="top"
              offset={[0, -14]}
              permanent
              className="!bg-zinc-900 !text-white !border-zinc-700 !rounded-lg !text-[10px] !px-2 !py-1 !shadow-lg"
            >
              {listing.address.split(",")[0]} ·{" "}
              {Math.round(listing.scores.composite)}
            </Tooltip>
            <Popup>
              <PinPopup
                listing={listing}
                addToWatchlist={addToWatchlist}
                addToShortlist={addToShortlist}
              />
            </Popup>
          </CircleMarker>
        ))}
    </>
  );
}

/** Shared popup content for all pins */
function PinPopup({
  listing,
  addToWatchlist,
  addToShortlist,
}: {
  listing: Listing;
  addToWatchlist: (l: Listing) => void;
  addToShortlist: (l: Listing) => void;
}) {
  return (
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
  );
}
