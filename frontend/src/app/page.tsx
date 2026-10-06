"use client";

import { useStore } from "@/lib/store";
import { useUrlState } from "@/lib/useUrlState";
import { useEscapeStack } from "@/lib/useEscapeStack";
import { useMounted } from "@/lib/useMounted";
import TopBar from "@/components/layout/TopBar";
import FilterSheet from "@/components/layout/FilterSheet";
import SettingsModal from "@/components/layout/SettingsModal";
import PinnedTray from "@/components/layout/PinnedTray";
import CompareModal from "@/components/compare/CompareModal";
import MapOverlay from "@/components/map/MapOverlay";
import MapPill from "@/components/map/MapPill";
import MapOverlayButtons from "@/components/map/MapOverlayButtons";
import ListPanel from "@/components/layout/ListPanel";
import DetailPanel from "@/components/layout/DetailPanel";
import dynamic from "next/dynamic";

const MapContainer = dynamic(
  () => import("react-leaflet").then((m) => m.MapContainer),
  { ssr: false },
);
const MapContent = dynamic(
  () => import("@/components/map/MapContent"),
  { ssr: false },
);

import { useCallback, useMemo } from "react";
import type { Listing } from "@/lib/types";

const NYC_CENTER: [number, number] = [40.73, -73.99];

export default function HomePage() {
  // Sync URL ↔ store (also handles initial data load)
  useUrlState();
  useEscapeStack();

  const activeTab = useStore((s) => s.activeTab);
  const listings = useStore((s) => s.listings);
  const watchlist = useStore((s) => s.watchlist);
  const shortlist = useStore((s) => s.shortlist);
  const skipped = useStore((s) => s.skipped);
  const selectedListingId = useStore((s) => s.selectedListingId);
  const addToWatchlist = useStore((s) => s.addToWatchlist);
  const addToShortlist = useStore((s) => s.addToShortlist);
  const mapColorOverlay = useStore((s) => s.mapColorOverlay);

  // Map state for desktop persistent map
  const mounted = useMounted();

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

  const highlightIds = useMemo((): Set<string> => {
    if (selectedListingId) return new Set([selectedListingId]);
    return new Set();
  }, [selectedListingId]);

  const handleTilesLoaded = useCallback(() => {}, []);

  return (
    <div className="h-dvh flex flex-col overflow-hidden bg-[#FAF7F2]">
      <TopBar />

      {/* === DESKTOP 3-panel layout (lg+) === */}
      <div className="hidden lg:grid lg:grid-cols-[380px_1fr_420px] flex-1 overflow-hidden">
        {/* Left panel — list */}
        <ListPanel />

        {/* Center panel — persistent map */}
        <div className="relative bg-gray-100 isolate z-0">
          {mounted && (
            <MapContainer
              center={NYC_CENTER}
              zoom={12}
              zoomControl={false}
              preferCanvas
              className="w-full h-full"
              style={{ background: "#F3F4F6" }}
            >
              <MapContent
                listings={displayListings}
                highlightIds={highlightIds}
                focusedId={selectedListingId}
                colorBy={mapColorOverlay}
                addToWatchlist={addToWatchlist}
                addToShortlist={addToShortlist}
                onTilesLoaded={handleTilesLoaded}
              />
            </MapContainer>
          )}
          <MapPill />
          <MapOverlayButtons />
        </div>

        {/* Right panel — detail */}
        <DetailPanel />
      </div>

      {/* === MOBILE layout (<lg) === */}
      <div className="flex flex-col flex-1 overflow-hidden lg:hidden">
        <ListPanel />
      </div>

      {/* Overlays (mobile map, compare, filter, settings) */}
      <div className="lg:hidden">
        <MapOverlay />
      </div>
      <CompareModal />
      <FilterSheet />
      <SettingsModal />
      <PinnedTray />
    </div>
  );
}
