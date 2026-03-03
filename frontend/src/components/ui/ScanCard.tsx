/**
 * ScanCard — Compact card tile for Scan mode and Queue grids.
 *
 * Shows thumbnail, price, bed count, neighborhood, composite score.
 * Optional checkbox for multi-select in queue views.
 */

"use client";

import type { Listing, ScoreDimension, ScoreGroupKey } from "@/lib/types";
import { DIMENSION_LABELS, GROUP_LABELS, SCORE_GROUP_KEYS } from "@/lib/types";
import { useStore } from "@/lib/store";
import {
  formatPrice,
  formatBeds,
  formatDaysOnMarket,
  neighborhoodAbbr,
  scoreColor,
  scoreRing,
  getScore,
  getGroupScore,
  scoreLabel,
} from "@/lib/utils";
import PhotoCarousel from "@/components/ui/PhotoCarousel";
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
  const kidsMode = useStore((s) => s.kidsMode);
  const isGroupSort = SCORE_GROUP_KEYS.includes(sortBy as ScoreGroupKey);
  const isSortedByDimension = sortBy !== "composite" && !isGroupSort;
  const sortScore = isGroupSort
    ? getGroupScore(listing.scores, sortBy as ScoreGroupKey, kidsMode)
    : isSortedByDimension
      ? getScore(listing.scores as unknown as Record<string, number | boolean | null>, sortBy as ScoreDimension)
      : listing.scores.composite;
  const displayScore = sortScore ?? listing.scores.composite;
  const sortLabel = isGroupSort
    ? GROUP_LABELS[sortBy as ScoreGroupKey]
    : isSortedByDimension
      ? DIMENSION_LABELS[sortBy as ScoreDimension]
      : null;

  return (
    <div
      className={clsx(
        "relative bg-white rounded-xl overflow-hidden cursor-pointer",
        "border transition-all duration-200 hover:border-gray-300 hover:shadow-lg",
        selected ? "border-amber-400 ring-1 ring-amber-400" : "border-[#E5E0D8]",
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
                ? "bg-amber-500 border-amber-500"
                : "border-gray-300 bg-white/80 backdrop-blur",
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

      {/* Thumbnail carousel */}
      <div className="bg-gray-100">
        {hasPhoto ? (
          <PhotoCarousel
            photos={listing.photos}
            alt={listing.address}
            aspect="aspect-[4/3]"
            showArrows={true}
            maxDots={5}
          />
        ) : (
          <div className="aspect-[4/3] w-full flex items-center justify-center text-gray-300">
            <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
            </svg>
          </div>
        )}
      </div>

      {/* Info */}
      <div className="p-3 space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-gray-900">
            {formatPrice(listing.price)}
          </span>
          <div className="flex items-center gap-1.5">
            {sortLabel && sortScore !== null && (
              <span className="text-[10px] text-gray-400 font-medium">
                {sortLabel}
              </span>
            )}
            <div
              className={clsx(
                "w-12 h-12 rounded-full flex items-center justify-center text-[9px] font-bold leading-tight text-center ring-1",
                scoreColor(displayScore),
                scoreRing(displayScore),
                "bg-white",
              )}
            >
              {scoreLabel(displayScore)}
            </div>
          </div>
        </div>
        <p className="text-xs text-gray-500">
          {formatBeds(listing.beds)} · {neighborhoodAbbr(listing.neighborhood)}
          {listing.sqft ? ` · ${listing.sqft} sf` : ""}
          {listing.days_on_market != null && (
            <span className="text-gray-400"> · {formatDaysOnMarket(listing.days_on_market)}</span>
          )}
        </p>
        {listing.data_quality && (
          <span
            className={clsx(
              "text-[9px] font-medium px-1.5 py-0.5 rounded-full w-fit",
              listing.data_quality === "very_limited"
                ? "bg-red-50 text-red-500"
                : "bg-amber-50 text-amber-600",
            )}
          >
            {listing.data_quality === "very_limited" ? "Very limited data" : "Limited data"}
          </span>
        )}
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
