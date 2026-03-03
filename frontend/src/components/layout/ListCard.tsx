/**
 * ListCard — Compact card for the left panel list.
 *
 * Shows: photo thumbnail, price, beds/baths, neighborhood, score badge.
 * Selected state: amber left border accent.
 */

"use client";

import type { Listing } from "@/lib/types";
import { formatPrice, formatBeds, scoreColor, scoreLabel, neighborhoodAbbr } from "@/lib/utils";
import clsx from "clsx";

interface ListCardProps {
  listing: Listing;
  selected: boolean;
  onClick: () => void;
}

export default function ListCard({ listing, selected, onClick }: ListCardProps) {
  const hasPhoto = listing.photos.length > 0;

  return (
    <button
      onClick={onClick}
      className={clsx(
        "w-full flex items-stretch gap-3 p-2 rounded-xl transition-all duration-200 text-left",
        "hover:bg-[#F3F0EB] group",
        selected
          ? "bg-amber-50 border border-amber-300 shadow-sm"
          : "bg-white border border-[#E5E0D8]",
      )}
    >
      {/* Thumbnail */}
      <div className="w-20 h-20 rounded-lg overflow-hidden shrink-0 bg-gray-100">
        {hasPhoto ? (
          <img
            src={listing.photos[0]}
            alt={listing.address}
            className="w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-gray-300">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
            </svg>
          </div>
        )}
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0 flex flex-col justify-between py-0.5">
        <div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-semibold text-gray-900 truncate">
              {formatPrice(listing.price)}/mo
            </span>
            {listing.no_fee && (
              <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 shrink-0">
                NO FEE
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500 truncate mt-0.5">
            {listing.address}{listing.unit ? `, ${listing.unit}` : ""}
          </p>
        </div>
        <div className="flex items-center justify-between mt-1">
          <span className="text-xs text-gray-400">
            {formatBeds(listing.beds)} · {neighborhoodAbbr(listing.neighborhood)}
            {listing.sqft ? ` · ${listing.sqft}sf` : ""}
          </span>
          {/* Score badge */}
          <span
            className={clsx(
              "text-xs font-bold px-2 py-0.5 rounded-full",
              scoreColor(listing.scores.composite),
              listing.scores.composite >= 75
                ? "bg-sky-50"
                : listing.scores.composite >= 50
                  ? "bg-violet-50"
                  : listing.scores.composite >= 25
                    ? "bg-orange-50"
                    : "bg-red-50",
            )}
          >
            {scoreLabel(listing.scores.composite)}
          </span>
        </div>
      </div>
    </button>
  );
}
