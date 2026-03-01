"use client";

import { useEffect } from "react";
import { useStore } from "@/lib/store";
import { useUrlState } from "@/lib/useUrlState";
import { useEscapeStack } from "@/lib/useEscapeStack";
import TopBar from "@/components/layout/TopBar";
import SortChips from "@/components/layout/SortChips";
import FilterSheet from "@/components/layout/FilterSheet";
import SettingsModal from "@/components/layout/SettingsModal";
import PinnedTray from "@/components/layout/PinnedTray";
import ExploreView from "@/components/explore/ExploreView";
import QueueView from "@/components/queue/QueueView";
import CompareModal from "@/components/compare/CompareModal";
import MapOverlay from "@/components/map/MapOverlay";

export default function HomePage() {
  const activeTab = useStore((s) => s.activeTab);
  const loadListings = useStore((s) => s.loadListings);
  const watchlist = useStore((s) => s.watchlist);
  const shortlist = useStore((s) => s.shortlist);

  useEffect(() => {
    loadListings();
  }, [loadListings]);

  // Sync URL ↔ store and handle Escape key for overlays
  useUrlState();
  useEscapeStack();

  return (
    <div className="h-dvh flex flex-col overflow-hidden bg-zinc-950">
      <TopBar />
      <SortChips />

      <main className="flex-1 flex flex-col overflow-hidden">
        {activeTab === "explore" && <ExploreView />}
        {activeTab === "watchlist" && (
          <QueueView listings={watchlist} queueType="watchlist" />
        )}
        {activeTab === "shortlist" && (
          <QueueView listings={shortlist} queueType="shortlist" />
        )}
      </main>

      {/* Overlays */}
      <MapOverlay />
      <CompareModal />
      <FilterSheet />
      <SettingsModal />
      <PinnedTray />
    </div>
  );
}
