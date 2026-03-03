/**
 * PinnedTray — Persistent bottom tray showing shortlisted listings.
 *
 * Circular photo thumbnails, max 5 visible with overflow count.
 * Tap to jump to listing. Always visible when shortlist is non-empty.
 */

"use client";

import { useStore } from "@/lib/store";
import { scoreRing, scoreLabel } from "@/lib/utils";
import clsx from "clsx";

export default function PinnedTray() {
  const shortlist = useStore((s) => s.shortlist);
  const setActiveTab = useStore((s) => s.setActiveTab);

  if (shortlist.length === 0) return null;

  const visible = shortlist.slice(0, 5);
  const overflow = shortlist.length - 5;

  return (
    <div className="fixed bottom-0 left-0 right-0 z-40 bg-white/90 backdrop-blur-md border-t border-[#E5E0D8]">
      <div className="flex items-center justify-center gap-3 h-16 px-4 max-w-7xl mx-auto">
        {visible.map((listing) => (
          <button
            key={listing.id}
            onClick={() => setActiveTab("shortlist")}
            className={clsx(
              "w-10 h-10 rounded-full overflow-hidden ring-2 transition-transform hover:scale-110",
              scoreRing(listing.scores.composite),
            )}
            title={`${listing.address} — ${scoreLabel(listing.scores.composite)}`}
          >
            {listing.photos.length > 0 ? (
              <img
                src={listing.photos[0]}
                alt={listing.address}
                className="w-full h-full object-cover"
                loading="lazy"
              />
            ) : (
              <div className="w-full h-full bg-gray-100 flex items-center justify-center text-gray-400 text-xs">
                📍
              </div>
            )}
          </button>
        ))}
        {overflow > 0 && (
          <button
            onClick={() => setActiveTab("shortlist")}
            className="w-10 h-10 rounded-full bg-gray-100 flex items-center justify-center text-xs text-gray-400 ring-2 ring-gray-200"
          >
            +{overflow}
          </button>
        )}
      </div>
    </div>
  );
}
