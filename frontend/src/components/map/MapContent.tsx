/**
 * MapContent — Inner map component rendered inside MapContainer.
 *
 * Split from MapOverlay so useMap() works (must be inside MapContainer).
 * Handles: tile layer, pins, clustering, highlighting, fly-to, fit-bounds.
 */

"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  TileLayer,
  CircleMarker,
  Popup,
  Tooltip,
  ZoomControl,
  useMap,
} from "react-leaflet";
import L from "leaflet";
import type { Listing, ScoreDimension } from "@/lib/types";
import { formatPrice, formatBeds, scoreLabel } from "@/lib/utils";
import HeatmapLayer from "./HeatmapLayer";

interface MapContentProps {
  listings: Listing[];
  highlightIds: Set<string>;
  focusedId: string | null;
  colorBy?: ScoreDimension | null;
  addToWatchlist: (l: Listing) => void;
  addToShortlist: (l: Listing) => void;
  onTilesLoaded?: () => void;
}

/** Group of co-located listings sharing the same pin position */
interface PinCluster {
  key: string;
  lat: number;
  lng: number;
  listings: Listing[];
  avgScore: number;
}

function pinColor(score: number): string {
  if (score >= 75) return "#0EA5E9";
  if (score >= 50) return "#8B5CF6";
  if (score >= 25) return "#F97316";
  return "#EF4444";
}

/** Group listings by rounded coordinates (~11 m precision) */
function clusterByLocation(listings: Listing[]): PinCluster[] {
  const groups = new Map<string, Listing[]>();
  for (const l of listings) {
    const key = `${l.latitude.toFixed(4)}_${l.longitude.toFixed(4)}`;
    const group = groups.get(key);
    if (group) group.push(l);
    else groups.set(key, [l]);
  }
  return Array.from(groups.entries()).map(([key, group]) => ({
    key,
    lat: group[0].latitude,
    lng: group[0].longitude,
    listings: group,
    avgScore:
      group.reduce((s, l) => s + l.scores.composite, 0) / group.length,
  }));
}

export default function MapContent({
  listings,
  highlightIds,
  focusedId,
  colorBy,
  addToWatchlist,
  addToShortlist,
  onTilesLoaded,
}: MapContentProps) {
  const map = useMap();
  const prevFocusedId = useRef<string | null>(null);
  const initialMount = useRef(true);
  const [tilesFired, setTilesFired] = useState(false);

  const hasHighlights = highlightIds.size > 0;

  // ── Tile load tracking ───────────────────────────────────────
  useEffect(() => {
    if (tilesFired) return;
    const handler = () => {
      setTilesFired(true);
      onTilesLoaded?.();
    };
    map.once("load", handler);
    // TileLayer fires its own "load" when all visible tiles are loaded,
    // but the map "load" event fires once the map is fully initialised.
    // As a fallback, also listen over a short timer.
    const timer = setTimeout(handler, 600);
    return () => {
      map.off("load", handler);
      clearTimeout(timer);
    };
  }, [map, onTilesLoaded, tilesFired]);

  // ── Navigate to focused listing ──────────────────────────────
  useEffect(() => {
    if (!focusedId || focusedId === prevFocusedId.current) return;
    prevFocusedId.current = focusedId;

    const listing = listings.find((l) => l.id === focusedId);
    if (!listing) return;

    if (initialMount.current) {
      // First open — MapContainer already centred via props, no animation
      initialMount.current = false;
      return;
    }
    // Subsequent focus changes — smooth fly
    map.flyTo([listing.latitude, listing.longitude], 15, { duration: 0.6 });
  }, [focusedId, listings, map]);

  // ── Fit bounds to all highlighted listings (compare mode) ────
  useEffect(() => {
    if (highlightIds.size < 2 || focusedId) return;

    const highlighted = listings.filter((l) => highlightIds.has(l.id));
    if (highlighted.length < 2) return;

    const bounds = L.latLngBounds(
      highlighted.map((l): L.LatLngTuple => [l.latitude, l.longitude]),
    );

    if (initialMount.current) {
      initialMount.current = false;
      map.fitBounds(bounds, { padding: [60, 60], maxZoom: 15, animate: false });
    } else {
      map.fitBounds(bounds, { padding: [60, 60], maxZoom: 15 });
    }
  }, [highlightIds, listings, map, focusedId]);

  // Mark initial mount done if nothing focused
  useEffect(() => {
    if (!focusedId && highlightIds.size < 2) {
      initialMount.current = false;
    }
  }, [focusedId, highlightIds]);

  // ── Cluster non-highlighted listings ─────────────────────────
  const nonHighlighted = useMemo(
    () => listings.filter((l) => !highlightIds.has(l.id)),
    [listings, highlightIds],
  );
  const clusters = useMemo(
    () => clusterByLocation(nonHighlighted),
    [nonHighlighted],
  );

  return (
    <>
      <TileLayer
        url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
      />
      <ZoomControl position="topright" />

      {/* ── Grid-based heatmap overlay ─────────────────────── */}
      {colorBy && <HeatmapLayer dimension={colorBy} />}

      {/* ── Non-highlighted pins (clustered) ─────────────────── */}
      {clusters.map((cluster) =>
        cluster.listings.length === 1 ? (
          // Single listing — normal pin
          <CircleMarker
            key={cluster.key}
            center={[cluster.lat, cluster.lng]}
            radius={hasHighlights ? 5 : 8}
            pathOptions={{
              color: pinColor(cluster.avgScore),
              fillColor: pinColor(cluster.avgScore),
              fillOpacity: hasHighlights ? 0.2 : 0.8,
              weight: hasHighlights ? 1 : 2,
            }}
          >
            <Popup>
              <PinPopup
                listing={cluster.listings[0]}
                addToWatchlist={addToWatchlist}
                addToShortlist={addToShortlist}
              />
            </Popup>
          </CircleMarker>
        ) : (
          // Multi-listing cluster
          <CircleMarker
            key={cluster.key}
            center={[cluster.lat, cluster.lng]}
            radius={hasHighlights ? 8 : 14}
            pathOptions={{
              color: pinColor(cluster.avgScore),
              fillColor: pinColor(cluster.avgScore),
              fillOpacity: hasHighlights ? 0.25 : 0.85,
              weight: hasHighlights ? 1 : 2,
            }}
          >
            <Tooltip
              direction="top"
              offset={[0, -10]}
              permanent
              className="cluster-count-tooltip"
            >
              {cluster.listings.length}
            </Tooltip>
            <Popup>
              <ClusterPopup
                cluster={cluster}
                addToWatchlist={addToWatchlist}
                addToShortlist={addToShortlist}
              />
            </Popup>
          </CircleMarker>
        ),
      )}

      {/* ── Highlighted pins — always individual, rendered last ─ */}
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
              className="!bg-white !text-gray-800 !border-[#E5E0D8] !rounded-lg !text-[10px] !px-2 !py-1 !shadow-lg"
            >
              {listing.address.split(",")[0]} ·{" "}
              {scoreLabel(listing.scores.composite)}
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

// ── Sub-components ─────────────────────────────────────────────

/** Popup for a single listing pin */
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
      <p className="font-semibold text-gray-900">{listing.address}</p>
      <p className="text-gray-500">
        {formatPrice(listing.price)} · {formatBeds(listing.beds)}
      </p>
      <p className="text-gray-500">
        Score: {scoreLabel(listing.scores.composite)}
      </p>
      <div className="flex gap-1 pt-1">
        <button
          onClick={() => addToWatchlist(listing)}
          className="text-[10px] px-2 py-0.5 bg-[#F3F0EB] rounded hover:bg-[#E5E0D8]"
        >
          + Watchlist
        </button>
        <button
          onClick={() => addToShortlist(listing)}
          className="text-[10px] px-2 py-0.5 bg-[#F3F0EB] rounded hover:bg-[#E5E0D8]"
        >
          ★ Shortlist
        </button>
      </div>
    </div>
  );
}

/** Popup for a cluster of multiple listings at the same location */
function ClusterPopup({
  cluster,
  addToWatchlist,
  addToShortlist,
}: {
  cluster: PinCluster;
  addToWatchlist: (l: Listing) => void;
  addToShortlist: (l: Listing) => void;
}) {
  return (
    <div className="text-xs min-w-[180px] max-w-[220px]">
      <p className="font-semibold text-gray-600 mb-2">
        {cluster.listings.length} apartments here
      </p>
      <div className="space-y-2 max-h-[200px] overflow-y-auto pr-1">
        {cluster.listings.map((listing) => (
          <div
            key={listing.id}
            className="border-t border-[#E5E0D8] pt-1.5 first:border-0 first:pt-0"
          >
            <p className="font-medium text-gray-900">{listing.address}</p>
            <p className="text-gray-400">
              {formatPrice(listing.price)} · {formatBeds(listing.beds)} ·{" "}
              {scoreLabel(listing.scores.composite)}
            </p>
            <div className="flex gap-1 pt-0.5">
              <button
                onClick={() => addToWatchlist(listing)}
                className="text-[10px] px-1.5 py-0.5 bg-[#F3F0EB] rounded hover:bg-[#E5E0D8]"
              >
                + Watch
              </button>
              <button
                onClick={() => addToShortlist(listing)}
                className="text-[10px] px-1.5 py-0.5 bg-[#F3F0EB] rounded hover:bg-[#E5E0D8]"
              >
                ★ Short
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
