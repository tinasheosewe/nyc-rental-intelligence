/**
 * FeedCard — Full-viewport single listing card for Explore Feed mode.
 *
 * Shows hero photo, address, price, composite score, and 5 group pills
 * above the fold. Collapsible grouped score breakdown, flags, and
 * building details below the fold on scroll.
 */

"use client";

import { useState } from "react";
import { useStore } from "@/lib/store";
import type { Listing, ScoreDimension, ScoreGroupKey } from "@/lib/types";
import {
  DIMENSION_LABELS,
  DIMENSION_BREAKOUT,
  SCORE_GROUPS,
  GROUP_BY_KEY,
  GROUP_LABELS,
  SCORE_GROUP_KEYS,
  getEffectiveGroups,
} from "@/lib/types";
import {
  formatPrice,
  formatBeds,
  formatBaths,
  formatDaysOnMarket,
  scoreColor,
  scoreBgMuted,
  getScore,
  getGroupScore,
  scoreLabel,
} from "@/lib/utils";
import ScoreBadge from "@/components/ui/ScoreBadge";
import ScoreBar from "@/components/ui/ScoreBar";
import FlagList from "@/components/ui/FlagList";
import PhotoCarousel from "@/components/ui/PhotoCarousel";
import { motion, AnimatePresence } from "framer-motion";
import clsx from "clsx";

interface FeedCardProps {
  listing: Listing;
  direction: number; // -1 = left, 1 = right for animation direction
}

export default function FeedCard({ listing, direction }: FeedCardProps) {
  const priorities = useStore((s) => s.priorities);
  const sortBy = useStore((s) => s.sortBy);
  const kidsMode = useStore((s) => s.kidsMode);
  const addToWatchlist = useStore((s) => s.addToWatchlist);
  const addToShortlist = useStore((s) => s.addToShortlist);
  const skipListing = useStore((s) => s.skipListing);
  const feedIndex = useStore((s) => s.feedIndex);
  const setFeedIndex = useStore((s) => s.setFeedIndex);
  const listings = useStore((s) => s.listings);
  const skipped = useStore((s) => s.skipped);
  const inWatchlist = useStore((s) => s.watchlist.some((l) => l.id === listing.id));
  const inShortlist = useStore((s) => s.shortlist.some((l) => l.id === listing.id));

  // Track which groups are expanded in the breakdown
  const [expandedGroups, setExpandedGroups] = useState<Set<ScoreGroupKey>>(new Set());

  const toggleGroup = (key: ScoreGroupKey) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // Sort label for secondary badge
  const sortLabel =
    sortBy !== "composite" && sortBy !== "price"
      ? GROUP_LABELS[sortBy as ScoreGroupKey] ?? sortBy
      : null;
  const sortScore =
    sortBy !== "composite" && sortBy !== "price"
      ? SCORE_GROUP_KEYS.includes(sortBy as ScoreGroupKey)
        ? getGroupScore(listing.scores, sortBy as ScoreGroupKey, kidsMode)
        : null
      : null;

  const handleSkip = () => {
    skipListing(listing.id);
    const available = listings.filter((l) => !skipped.has(l.id) && l.id !== listing.id);
    if (available.length > 0) {
      const nextIdx = listings.indexOf(available[0]);
      if (nextIdx >= 0) setFeedIndex(nextIdx);
    }
  };

  const handleSave = () => {
    addToWatchlist(listing);
    if (feedIndex < listings.length - 1) {
      setFeedIndex(feedIndex + 1);
    }
  };

  const hasPhoto = listing.photos.length > 0;

  // Groups ordered by user priorities (with schools filtered when kidsMode off)
  const effectiveGroupByKey = getEffectiveGroups(kidsMode).reduce(
    (acc, g) => ({ ...acc, [g.key]: g }),
    {} as Record<string, typeof SCORE_GROUPS[number]>,
  );
  const orderedGroups = priorities.map((gk) => effectiveGroupByKey[gk]).filter(Boolean);

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
        {/* Hero photo carousel */}
        <div className="relative w-full shrink-0">
          {hasPhoto ? (
            <PhotoCarousel
              photos={listing.photos}
              alt={listing.address}
              aspect="aspect-[16/9]"
            />
          ) : (
            <div className="w-full aspect-[16/9] bg-zinc-800 flex items-center justify-center text-zinc-600">
              <svg className="w-16 h-16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 22V12h6v10" />
              </svg>
            </div>
          )}
          {listing.no_fee && (
            <span className="absolute top-3 left-3 bg-green-500 text-white text-xs font-bold px-2 py-1 rounded z-10">
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
                {listing.sqft && listing.price
                  ? ` · $${(listing.price / listing.sqft).toFixed(2)}/sqft`
                  : ""}
              </p>
              <p className="text-sm text-zinc-500 mt-0.5">
                {listing.address}
                {listing.unit ? `, ${listing.unit}` : ""}
              </p>
              <p className="text-xs text-zinc-600 mt-0.5">
                {listing.neighborhood}, {listing.borough}
                {listing.days_on_market != null && (
                  <span className="ml-2 text-zinc-500">
                    · {formatDaysOnMarket(listing.days_on_market)}
                  </span>
                )}
              </p>
            </div>
            <div className="flex flex-col items-end gap-1">
              <ScoreBadge score={listing.scores.composite} size="lg" label="Score" />
              {sortLabel && sortScore !== null && (
                <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-zinc-800 text-zinc-300">
                  {sortLabel}{" "}
                  <span className={scoreColor(sortScore)}>
                    {scoreLabel(sortScore)}
                  </span>
                </span>
              )}
            </div>
          </div>

          {/* Group score pills */}
          <div className="flex items-center gap-2 flex-wrap">
            {orderedGroups.map((group) => {
              const gs = getGroupScore(listing.scores, group.key, kidsMode);
              if (gs === null) return null;
              return (
                <span
                  key={group.key}
                  className={clsx(
                    "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium",
                    scoreBgMuted(gs),
                    scoreColor(gs),
                  )}
                >
                  {group.icon} {group.label} {scoreLabel(gs)}
                </span>
              );
            })}
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

          {/* Grouped score breakdown */}
          <div className="space-y-1">
            <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">
              Score Breakdown
            </h3>
            {orderedGroups.map((group) => {
              const gs = getGroupScore(listing.scores, group.key, kidsMode);
              if (gs === null) return null;
              const isExpanded = expandedGroups.has(group.key);

              return (
                <div key={group.key} className="rounded-lg border border-zinc-800/50 overflow-hidden">
                  {/* Group header — always visible */}
                  <button
                    onClick={() => toggleGroup(group.key)}
                    className="w-full flex items-center gap-3 px-3 py-2.5 hover:bg-zinc-800/50 transition-colors"
                  >
                    <span className="text-base">{group.icon}</span>
                    <span className="flex-1 text-left text-sm font-medium text-zinc-300">
                      {group.label}
                    </span>
                    <span className={clsx("text-sm font-bold", scoreColor(gs))}>
                      {scoreLabel(gs)}
                    </span>
                    <span className="text-xs text-zinc-600 w-4 text-center select-none">
                      {isExpanded ? "▾" : "▸"}
                    </span>
                  </button>

                  {/* Expanded: individual dimension bars */}
                  {isExpanded && (
                    <div className="px-3 pb-3 pt-1 space-y-2 border-t border-zinc-800/50">
                      {group.dimensions.map((dim) => {
                        const score = getScore(
                          listing.scores as unknown as Record<string, number | boolean | null>,
                          dim,
                        );
                        if (score === null) return null;
                        const trend =
                          dim === "crime"
                            ? listing.trends.crime_direction
                            : dim === "noise"
                              ? listing.trends.noise_direction
                              : undefined;
                        return (
                          <ScoreBar
                            key={dim}
                            label={DIMENSION_LABELS[dim]}
                            score={score}
                            trend={trend}
                            dim={dim}
                            breakout={DIMENSION_BREAKOUT[dim]}
                            componentValues={listing.score_components?.[dim]}
                          />
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
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

          {/* Description */}
          {listing.description && (
            <>
              <div className="space-y-2">
                <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                  Description
                </h3>
                <p className="text-sm text-zinc-400 whitespace-pre-line leading-relaxed">
                  {listing.description}
                </p>
              </div>
              <div className="border-t border-zinc-800" />
            </>
          )}

          {/* Amenities */}
          {listing.amenities && listing.amenities.length > 0 && (
            <>
              <div className="space-y-2">
                <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                  Amenities
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {listing.amenities.map((a, i) => (
                    <span
                      key={i}
                      className="px-2 py-0.5 rounded-full text-xs font-medium bg-zinc-800 text-zinc-300 border border-zinc-700"
                    >
                      {a}
                    </span>
                  ))}
                </div>
              </div>
              <div className="border-t border-zinc-800" />
            </>
          )}

          {/* Price History */}
          {listing.price_history && listing.price_history.length > 0 && (
            <>
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                    Price History
                  </h3>
                  {listing.relist_count > 1 && (
                    <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-400">
                      Relisted {listing.relist_count}x
                    </span>
                  )}
                </div>
                <div className="space-y-1.5">
                  {listing.price_history.map((ph, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between text-sm"
                    >
                      <span className="text-zinc-500">{ph.date}</span>
                      <span className="text-zinc-400 font-medium">{ph.price}</span>
                      <span className="text-zinc-500 text-xs truncate max-w-[140px]">{ph.event}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div className="border-t border-zinc-800" />
            </>
          )}

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
              {listing.building.stories != null && listing.building.stories > 0 && (
                <>
                  <dt className="text-zinc-500">Stories</dt>
                  <dd className="text-zinc-300">{listing.building.stories}</dd>
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
