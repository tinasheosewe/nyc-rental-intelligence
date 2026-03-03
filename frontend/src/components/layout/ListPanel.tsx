/**
 * ListPanel — Left panel in 3-panel layout / main content on mobile.
 *
 * Contains:
 *   - Tab switcher (Explore / Watchlist / Shortlist) — desktop only
 *   - SortChips
 *   - Scrollable list of ListCards (desktop) or full ExploreView (mobile)
 */

"use client";

import { useRef, useEffect, useMemo } from "react";
import { useStore } from "@/lib/store";
import type { QueueTab } from "@/lib/types";
import SortChips from "./SortChips";
import ListCard from "./ListCard";
import ExploreView from "@/components/explore/ExploreView";
import QueueView from "@/components/queue/QueueView";
import clsx from "clsx";

const TABS: { key: QueueTab; label: string }[] = [
  { key: "explore", label: "Explore" },
  { key: "watchlist", label: "Watchlist" },
  { key: "shortlist", label: "Shortlist" },
];

export default function ListPanel() {
  const activeTab = useStore((s) => s.activeTab);
  const setActiveTab = useStore((s) => s.setActiveTab);
  const listings = useStore((s) => s.listings);
  const watchlist = useStore((s) => s.watchlist);
  const shortlist = useStore((s) => s.shortlist);
  const skipped = useStore((s) => s.skipped);
  const isLoading = useStore((s) => s.isLoading);
  const isLoadingMore = useStore((s) => s.isLoadingMore);
  const hasMore = useStore((s) => s.hasMore);
  const loadMore = useStore((s) => s.loadMore);
  const totalListings = useStore((s) => s.totalListings);
  const selectedListingId = useStore((s) => s.selectedListingId);
  const setSelectedListingId = useStore((s) => s.setSelectedListingId);

  const counts: Record<QueueTab, number> = {
    explore: 0,
    watchlist: watchlist.length,
    shortlist: shortlist.length,
  };

  const visible = useMemo(() => {
    switch (activeTab) {
      case "watchlist":
        return watchlist;
      case "shortlist":
        return shortlist;
      default:
        return listings.filter((l) => !skipped.has(l.id));
    }
  }, [activeTab, listings, watchlist, shortlist, skipped]);

  // Infinite scroll sentinel
  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasMore && !isLoadingMore && activeTab === "explore") {
          loadMore();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, isLoadingMore, loadMore, isLoading, activeTab]);

  return (
    <>
      {/* === Desktop list panel === */}
      <div className="hidden lg:flex lg:flex-col border-r border-[#E5E0D8] bg-white overflow-hidden">
        {/* Desktop tabs */}
        <div className="px-4 pt-3 pb-2">
          <nav className="grid grid-cols-3 gap-1 bg-[#F3F0EB] rounded-lg p-1">
            {TABS.map(({ key, label }) => (
              <button
                key={key}
                onClick={() => setActiveTab(key)}
                className={clsx(
                  "px-3 py-1.5 rounded-md text-sm font-medium transition-all duration-200 text-center",
                  activeTab === key
                    ? "bg-white text-gray-900 shadow-sm"
                    : "text-gray-500 hover:text-gray-700",
                )}
              >
                {label}
                {counts[key] > 0 && (
                  <span className="ml-1 text-xs text-gray-400">{counts[key]}</span>
                )}
              </button>
            ))}
          </nav>
        </div>

        <SortChips />

        {/* Scrollable list */}
        <div className="flex-1 overflow-y-auto px-3 pb-4">
          {isLoading ? (
            <div className="flex items-center justify-center h-40">
              <div className="w-7 h-7 border-2 border-gray-200 border-t-amber-500 rounded-full animate-spin" />
            </div>
          ) : visible.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-40 text-gray-400 gap-2">
              <p className="text-sm">
                {activeTab === "explore"
                  ? "No listings match your criteria"
                  : activeTab === "watchlist"
                    ? "Save listings to add them here"
                    : "Star your favorites to shortlist them"}
              </p>
            </div>
          ) : (
            <>
              <p className="text-xs text-gray-400 mb-2 px-1">
                {activeTab === "explore"
                  ? `Showing ${visible.length} of ${totalListings}`
                  : `${visible.length} listing${visible.length !== 1 ? "s" : ""}`}
              </p>
              <div className="space-y-2">
                {visible.map((listing) => (
                  <ListCard
                    key={listing.id}
                    listing={listing}
                    selected={selectedListingId === listing.id}
                    onClick={() => setSelectedListingId(listing.id)}
                  />
                ))}
              </div>

              {/* Sentinel for infinite scroll */}
              <div ref={sentinelRef} className="h-1" />

              {isLoadingMore && (
                <div className="flex justify-center py-4">
                  <div className="w-5 h-5 border-2 border-gray-200 border-t-amber-500 rounded-full animate-spin" />
                </div>
              )}

              {!hasMore && activeTab === "explore" && visible.length > 0 && (
                <p className="text-center text-xs text-gray-400 py-3">
                  All {totalListings} listings loaded
                </p>
              )}
            </>
          )}
        </div>
      </div>

      {/* === Mobile: full-width content === */}
      <div className="flex flex-col flex-1 overflow-hidden lg:hidden">
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
      </div>
    </>
  );
}
