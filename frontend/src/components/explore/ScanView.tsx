/**
 * ScanView — Explore Scan mode (compact grid).
 *
 * Tapping any card switches to Feed mode at that listing's position.
 */

"use client";

import { useStore } from "@/lib/store";
import ScanCard from "@/components/ui/ScanCard";

export default function ScanView() {
  const listings = useStore((s) => s.listings);
  const skipped = useStore((s) => s.skipped);
  const isLoading = useStore((s) => s.isLoading);
  const setViewMode = useStore((s) => s.setViewMode);
  const setFeedIndex = useStore((s) => s.setFeedIndex);

  const visible = listings.filter((l) => !skipped.has(l.id));

  const handleCardClick = (listingId: string) => {
    const idx = listings.findIndex((l) => l.id === listingId);
    if (idx >= 0) {
      setFeedIndex(idx);
      setViewMode("feed");
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
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 max-w-7xl mx-auto">
        {visible.map((listing) => (
          <ScanCard
            key={listing.id}
            listing={listing}
            onClick={() => handleCardClick(listing.id)}
          />
        ))}
      </div>
    </div>
  );
}
