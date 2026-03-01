/**
 * ScanCard — Compact card tile for Scan mode and Queue grids.
 *
 * Shows thumbnail, price, bed count, neighborhood, composite score.
 * Optional checkbox for multi-select in queue views.
 */

"use client";

import type { Listing, ScoreDimension } from "@/lib/types";
import { DIMENSION_LABELS } from "@/lib/types";
import { useStore } from "@/lib/store";
import {
  formatPrice,
  formatBeds,
  formatDaysOnMarket,
  neighborhoodAbbr,
  scoreColor,
  scoreRing,
  getScore,
} from "@/lib/utils";
import clsx from "clsx";

interface ScanCardProps {
  listing: Listing;
  onClick: () => void;
  selectable?: boolean;
  selected?: boolean;
  onSelect?: () => void;
}

export default function ScanCard({
  listing,
  onClick,
  selectable = false,
  selected = false,
  onSelect,
}: ScanCardProps) {
  const hasPhoto = listing.photos.length > 0;
  const sortBy = useStore((s) => s.sortBy);
  const isSortedByDimension = sortBy !== "composite";
  const sortScore = isSortedByDimension
    ? getScore(listing.scores as unknown as Record<string, number | boolean>, sortBy as ScoreDimension) ?? 0
    : listing.scores.composite;

  return (
    <div
      className={clsx(
        "relative bg-zinc-900 rounded-xl overflow-hidden cursor-pointer",
        "border transition-all duration-200 hover:border-zinc-600 hover:shadow-lg",
        selected ? "border-blue-500 ring-1 ring-blue-500" : "border-zinc-800",
      )}
      onClick={onClick}
    >
      {/* Checkbox */}
      {selectable && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onSelect?.();
          }}
          className="absolute top-2 left-2 z-10"
        >
          <div
            className={clsx(
              "w-5 h-5 rounded border-2 flex items-center justify-center transition-colors",
              selected
                ? "bg-blue-500 border-blue-500"
                : "border-zinc-500 bg-zinc-900/80 backdrop-blur",
            )}
          >
            {selected && (
              <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
              </svg>
            )}
          </div>
        </button>
      )}

      {/* Thumbnail */}
      <div className="aspect-[4/3] bg-zinc-800">
        {hasPhoto ? (
          <img
            src={listing.photos[0]}
            alt={listing.address}
            className="w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-zinc-700">
            <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
            </svg>
          </div>
        )}
      </div>

      {/* Info */}
      <div className="p-3 space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-white">
            {formatPrice(listing.price)}
          </span>
          <div className="flex items-center gap-1.5">
            {isSortedByDimension && (
              <span className="text-[10px] text-zinc-500 font-medium">
                {DIMENSION_LABELS[sortBy as ScoreDimension]}
              </span>
            )}
            <div
              className={clsx(
                "w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold ring-1",
                scoreColor(sortScore),
                scoreRing(sortScore),
                "bg-zinc-900",
              )}
            >
              {Math.round(sortScore)}
            </div>
          </div>
        </div>
        <p className="text-xs text-zinc-400">
          {formatBeds(listing.beds)} · {neighborhoodAbbr(listing.neighborhood)}
          {listing.days_on_market != null && (
            <span className="text-zinc-500"> · {formatDaysOnMarket(listing.days_on_market)}</span>
          )}
        </p>
      </div>

      {/* No-fee badge */}
      {listing.no_fee && (
        <span className="absolute top-2 right-2 bg-green-500 text-white text-[10px] font-bold px-1.5 py-0.5 rounded">
          NO FEE
        </span>
      )}
    </div>
  );
}
