/**
 * FeedCard — Full-viewport single listing card for Explore Feed mode.
 *
 * Shows hero photo, address, price, composite score, top-3 priority
 * pills above the fold. Score breakdown, flags, and building details
 * below the fold on scroll.
 */

"use client";

import { useStore } from "@/lib/store";
import type { Listing, ScoreDimension } from "@/lib/types";
import { DIMENSION_LABELS, DIMENSION_BREAKOUT } from "@/lib/types";
import {
  formatPrice,
  formatBeds,
  formatBaths,
  scoreColor,
  getScore,
} from "@/lib/utils";
import ScoreBadge from "@/components/ui/ScoreBadge";
import ScorePill from "@/components/ui/ScorePill";
import ScoreBar from "@/components/ui/ScoreBar";
import FlagList from "@/components/ui/FlagList";
import { motion, AnimatePresence } from "framer-motion";

interface FeedCardProps {
  listing: Listing;
  direction: number; // -1 = left, 1 = right for animation direction
}

export default function FeedCard({ listing, direction }: FeedCardProps) {
  const priorities = useStore((s) => s.priorities);
  const sortBy = useStore((s) => s.sortBy);
  const addToWatchlist = useStore((s) => s.addToWatchlist);
  const addToShortlist = useStore((s) => s.addToShortlist);
  const skipListing = useStore((s) => s.skipListing);
  const feedIndex = useStore((s) => s.feedIndex);
  const setFeedIndex = useStore((s) => s.setFeedIndex);
  const listings = useStore((s) => s.listings);
  const skipped = useStore((s) => s.skipped);
  const inWatchlist = useStore((s) => s.watchlist.some((l) => l.id === listing.id));
  const inShortlist = useStore((s) => s.shortlist.some((l) => l.id === listing.id));

  const top3 = priorities.slice(0, 3) as ScoreDimension[];

  // Score dimensions with trend info
  const allDimensions = priorities.map((dim) => ({
    key: dim,
    label: DIMENSION_LABELS[dim],
    score: getScore(listing.scores as unknown as Record<string, number | boolean>, dim),
    trend:
      dim === "crime"
        ? listing.trends.crime_direction
        : dim === "noise"
          ? listing.trends.noise_direction
          : undefined,
  }));

  const handleSkip = () => {
    skipListing(listing.id);
    // Advance to next non-skipped listing
    const available = listings.filter((l) => !skipped.has(l.id) && l.id !== listing.id);
    if (available.length > 0) {
      const nextIdx = listings.indexOf(available[0]);
      if (nextIdx >= 0) setFeedIndex(nextIdx);
    }
  };

  const handleSave = () => {
    addToWatchlist(listing);
    // Advance
    if (feedIndex < listings.length - 1) {
      setFeedIndex(feedIndex + 1);
    }
  };

  const hasPhoto = listing.photos.length > 0;

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={listing.id}
        initial={{ x: direction * 300, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        exit={{ x: direction * -300, opacity: 0 }}
        transition={{ duration: 0.3, ease: "easeOut" }}
        className="flex flex-col h-full overflow-y-auto pb-24"
      >
        {/* Hero photo */}
        <div className="relative w-full aspect-[16/9] bg-zinc-800 shrink-0">
          {hasPhoto ? (
            <img
              src={listing.photos[0]}
              alt={listing.address}
              className="w-full h-full object-cover"
              loading="lazy"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-zinc-600">
              <svg className="w-16 h-16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 22V12h6v10" />
              </svg>
            </div>
          )}
          {/* No-fee badge */}
          {listing.no_fee && (
            <span className="absolute top-3 left-3 bg-green-500 text-white text-xs font-bold px-2 py-1 rounded">
              NO FEE
            </span>
          )}
        </div>

        {/* Above the fold content */}
        <div className="px-5 pt-4 space-y-4">
          {/* Address + price */}
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">
                {formatPrice(listing.price)}/mo
              </h2>
              <p className="text-sm text-zinc-400">
                {formatBeds(listing.beds)} · {formatBaths(listing.baths)}
                {listing.sqft ? ` · ${listing.sqft} sqft` : ""}
              </p>
              <p className="text-sm text-zinc-500 mt-0.5">
                {listing.address}
                {listing.unit ? `, ${listing.unit}` : ""}
              </p>
              <p className="text-xs text-zinc-600 mt-0.5">
                {listing.neighborhood}, {listing.borough}
              </p>
            </div>
            <div className="flex flex-col items-end gap-1">
              <ScoreBadge score={listing.scores.composite} size="lg" label="Score" />
              {sortBy !== "composite" && (
                <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-zinc-800 text-zinc-300">
                  {DIMENSION_LABELS[sortBy as ScoreDimension]}{" "}
                  <span className={scoreColor(
                    getScore(listing.scores as unknown as Record<string, number | boolean>, sortBy as ScoreDimension) ?? 0
                  )}>
                    {Math.round(
                      getScore(listing.scores as unknown as Record<string, number | boolean>, sortBy as ScoreDimension) ?? 0
                    )}
                  </span>
                </span>
              )}
            </div>
          </div>

          {/* Top-3 priority pills */}
          <div className="flex items-center gap-2 flex-wrap">
            {top3.map((dim) => (
              <ScorePill
                key={dim}
                label={DIMENSION_LABELS[dim]}
                score={getScore(listing.scores as unknown as Record<string, number | boolean>, dim)}
                trend={
                  dim === "crime"
                    ? listing.trends.crime_direction
                    : dim === "noise"
                      ? listing.trends.noise_direction
                      : undefined
                }
              />
            ))}
            {listing.scores.rent_stabilized && (
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-500/20 text-blue-400">
                Rent Stabilized
              </span>
            )}
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleSkip}
              className="flex-1 py-2.5 rounded-xl bg-zinc-800 text-zinc-400 font-medium text-sm hover:bg-zinc-700 transition-colors"
            >
              Skip
            </button>
            <button
              onClick={handleSave}
              disabled={inWatchlist || inShortlist}
              className={
                inWatchlist || inShortlist
                  ? "flex-1 py-2.5 rounded-xl bg-zinc-800 text-zinc-600 font-medium text-sm cursor-not-allowed"
                  : "flex-1 py-2.5 rounded-xl bg-white text-zinc-900 font-medium text-sm hover:bg-zinc-200 transition-colors"
              }
            >
              {inWatchlist ? "In Watchlist" : inShortlist ? "In Shortlist" : "Save"}
            </button>
            {!inShortlist && (
              <button
                onClick={() => addToShortlist(listing)}
                className="p-2.5 rounded-xl bg-zinc-800 text-yellow-400 hover:bg-zinc-700 transition-colors"
                title="Add to Shortlist"
              >
                ★
              </button>
            )}
          </div>

          {/* Divider */}
          <div className="border-t border-zinc-800" />

          {/* Score breakdown */}
          <div className="space-y-2">
            <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
              Score Breakdown
            </h3>
            <div className="space-y-2">
              {allDimensions.map(({ key, label, score, trend }) => (
                <ScoreBar
                  key={key}
                  label={label}
                  score={score}
                  trend={trend}
                  breakout={DIMENSION_BREAKOUT[key]}
                  componentValues={listing.score_components?.[key]}
                />
              ))}
            </div>
          </div>

          {/* Divider */}
          <div className="border-t border-zinc-800" />

          {/* Insights / Flags */}
          {listing.flags.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                Insights
              </h3>
              <FlagList flags={listing.flags} />
            </div>
          )}

          {/* Divider */}
          <div className="border-t border-zinc-800" />

          {/* Building details */}
          <div className="space-y-2 pb-6">
            <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
              Building Details
            </h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              {listing.building.owner && (
                <>
                  <dt className="text-zinc-500">Owner</dt>
                  <dd className="text-zinc-300">{listing.building.owner}</dd>
                </>
              )}
              {listing.building.year_built && (
                <>
                  <dt className="text-zinc-500">Year Built</dt>
                  <dd className="text-zinc-300">{listing.building.year_built}</dd>
                </>
              )}
              {listing.building.total_units != null && listing.building.total_units > 0 && (
                <>
                  <dt className="text-zinc-500">Units</dt>
                  <dd className="text-zinc-300">{listing.building.total_units}</dd>
                </>
              )}
              <dt className="text-zinc-500">Building Violations</dt>
              <dd className="text-zinc-300">{listing.building.open_violations} open</dd>
              <dt className="text-zinc-500">HPD Complaints</dt>
              <dd className="text-zinc-300">{listing.building.hpd_complaints_12mo} (12mo)</dd>
            </dl>
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
