/**
 * QueueView — Grid view for Watchlist and Shortlist.
 *
 * Functionally identical for both queues — receives a listing array
 * and queue-specific action callbacks. Supports multi-select for
 * compare and batch operations.
 */

"use client";

import { useCallback } from "react";
import { useStore } from "@/lib/store";
import { pushListingUrl } from "@/lib/useUrlState";
import type { Listing } from "@/lib/types";
import ScanCard from "@/components/ui/ScanCard";
import FeedCard from "@/components/explore/FeedCard";
import clsx from "clsx";

interface QueueViewProps {
  listings: Listing[];
  queueType: "watchlist" | "shortlist";
}

export default function QueueView({ listings, queueType }: QueueViewProps) {
  const expandedId = useStore((s) => s.expandedQueueId);
  const setExpandedId = useStore((s) => s.setExpandedQueueId);
  const compareIds = useStore((s) => s.compareIds);
  const toggleCompareId = useStore((s) => s.toggleCompareId);
  const setCompareOpen = useStore((s) => s.setCompareOpen);
  const removeFromWatchlist = useStore((s) => s.removeFromWatchlist);
  const removeFromShortlist = useStore((s) => s.removeFromShortlist);
  const moveToShortlist = useStore((s) => s.moveToShortlist);
  const moveToWatchlist = useStore((s) => s.moveToWatchlist);

  const selectedCount = Array.from(compareIds).filter((id) =>
    listings.some((l) => l.id === id),
  ).length;

  const handleRemove = useCallback(
    (id: string) => {
      if (queueType === "watchlist") removeFromWatchlist(id);
      else removeFromShortlist(id);
    },
    [queueType, removeFromWatchlist, removeFromShortlist],
  );

  const handleMove = useCallback(
    (id: string) => {
      if (queueType === "watchlist") moveToShortlist(id);
      else moveToWatchlist(id);
    },
    [queueType, moveToShortlist, moveToWatchlist],
  );

  // Expanded single-card view within queue
  if (expandedId) {
    const listing = listings.find((l) => l.id === expandedId);
    if (!listing) {
      setExpandedId(null);
      return null;
    }

    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Back bar */}
        <div className="flex items-center justify-between px-4 py-2 border-b border-[#E5E0D8]">
          <button
            onClick={() => setExpandedId(null)}
            className="text-sm text-gray-500 hover:text-gray-900 flex items-center gap-1"
          >
            ← Back to {queueType === "watchlist" ? "Watchlist" : "Shortlist"}
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={() => handleMove(listing.id)}
              className="text-xs px-3 py-1.5 rounded-lg bg-[#F3F0EB] text-gray-600 hover:bg-gray-200"
            >
              Move to {queueType === "watchlist" ? "Shortlist" : "Watchlist"}
            </button>
            <button
              onClick={() => {
                handleRemove(listing.id);
                setExpandedId(null);
              }}
              className="text-xs px-3 py-1.5 rounded-lg bg-red-500/10 text-red-400 hover:bg-red-500/20"
            >
              Remove
            </button>
          </div>
        </div>
        <FeedCard listing={listing} direction={0} />
      </div>
    );
  }

  // Empty state
  if (listings.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-gray-400 gap-3">
        <svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
        </svg>
        <p className="text-sm">
          {queueType === "watchlist"
            ? "Save listings from Explore to add them here"
            : "Star your favorite listings to shortlist them"}
        </p>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Batch toolbar */}
      {selectedCount > 0 && (
        <div className="flex items-center justify-between px-4 py-2 bg-white border-b border-[#E5E0D8]">
          <span className="text-sm text-gray-500">
            {selectedCount} selected
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setCompareOpen(true)}
              disabled={selectedCount < 2}
              className={clsx(
                "text-xs px-3 py-1.5 rounded-lg font-medium",
                selectedCount >= 2
                  ? "bg-white text-gray-900 hover:bg-gray-100"
                  : "bg-[#F3F0EB] text-gray-400 cursor-not-allowed",
              )}
            >
              Compare ({selectedCount})
            </button>
          </div>
        </div>
      )}

      {/* Grid */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 max-w-7xl mx-auto">
          {listings.map((listing) => (
            <ScanCard
              key={listing.id}
              listing={listing}
              onClick={() => {
                setExpandedId(listing.id);
                pushListingUrl(listing.id);
              }}
              selectable
              selected={compareIds.has(listing.id)}
              onSelect={() => toggleCompareId(listing.id)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
