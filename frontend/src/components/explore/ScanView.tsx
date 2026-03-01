/**
 * ScanView — Explore Scan mode (compact grid).
 *
 * Tapping any card switches to Feed mode at that listing's position.
 * Uses IntersectionObserver for infinite scroll (24 cards per page).
 */

"use client";

import { useRef, useEffect } from "react";
import { useStore } from "@/lib/store";
import { pushListingUrl } from "@/lib/useUrlState";
import ScanCard from "@/components/ui/ScanCard";

export default function ScanView() {
  const listings = useStore((s) => s.listings);
  const skipped = useStore((s) => s.skipped);
  const isLoading = useStore((s) => s.isLoading);
  const isLoadingMore = useStore((s) => s.isLoadingMore);
  const hasMore = useStore((s) => s.hasMore);
  const loadMore = useStore((s) => s.loadMore);
  const setViewMode = useStore((s) => s.setViewMode);
  const setFeedIndex = useStore((s) => s.setFeedIndex);
  const totalListings = useStore((s) => s.totalListings);

  const visible = listings.filter((l) => !skipped.has(l.id));
  const sentinelRef = useRef<HTMLDivElement>(null);

  // Infinite scroll via IntersectionObserver.
  // Depends on isLoading so the observer is re-created after loadListings()
  // completes (the sentinel DOM element is unmounted during loading and a
  // new one is mounted when the grid renders — the old observer would be
  // watching a stale element otherwise).
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasMore && !isLoadingMore) {
          loadMore();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, isLoadingMore, loadMore, isLoading]);

  const handleCardClick = (listingId: string) => {
    const idx = listings.findIndex((l) => l.id === listingId);
    if (idx >= 0) {
      setFeedIndex(idx);
      setViewMode("feed");
      pushListingUrl(listingId);
    }
  };

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-zinc-700 border-t-white rounded-full animate-spin" />
      </div>
    );
  }

  if (visible.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-zinc-500 gap-3">
        <p className="text-sm">No listings match your criteria</p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4">
      {/* Count */}
      <p className="text-xs text-zinc-600 mb-3 text-center">
        Showing {visible.length} of {totalListings}
      </p>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 max-w-7xl mx-auto">
        {visible.map((listing) => (
          <ScanCard
            key={listing.id}
            listing={listing}
            onClick={() => handleCardClick(listing.id)}
          />
        ))}
      </div>

      {/* Sentinel for infinite scroll */}
      <div ref={sentinelRef} className="h-1" />

      {/* Loading more indicator */}
      {isLoadingMore && (
        <div className="flex justify-center py-6">
          <div className="w-6 h-6 border-2 border-zinc-700 border-t-white rounded-full animate-spin" />
        </div>
      )}

      {/* End of results */}
      {!hasMore && visible.length > 0 && (
        <p className="text-center text-xs text-zinc-600 py-4">
          All {totalListings} listings loaded
        </p>
      )}
    </div>
  );
}
