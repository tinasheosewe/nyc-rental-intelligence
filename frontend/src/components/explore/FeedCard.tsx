/**
 * FeedCard — Full-viewport single listing card for Explore Feed mode.
 *
 * Shows hero photo, address, price, composite score, and 5 group pills
 * above the fold. Below the fold: score breakdown, insights, transit,
 * neighborhood, POIs, amenities, building, comparables, and more.
 */

"use client";

import { useState, useEffect } from "react";
import { useStore } from "@/lib/store";
import { fetchListing } from "@/lib/api";
import type {
  Listing,
  ScoreDimension,
  ScoreGroupKey,
  TransitStation,
  ComparableListing,
  POI,
} from "@/lib/types";
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
  mtaRouteColor,
  poiIcon,
  groupIcon,
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

  // Lazy-load detail data (transit stations, comparables, neighborhood info, etc.)
  const [detail, setDetail] = useState<Listing | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setDetailLoading(true);
    fetchListing(listing.id)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [listing.id]);

  // Use detail data when available, otherwise fall back to listing
  const l = detail ?? listing;

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
              {listing.data_quality && (
                <span
                  className={clsx(
                    "text-[10px] font-medium px-2 py-0.5 rounded-full",
                    listing.data_quality === "very_limited"
                      ? "bg-red-500/10 text-red-400"
                      : "bg-yellow-500/10 text-yellow-400",
                  )}
                >
                  {listing.data_quality === "very_limited" ? "Very limited data" : "Limited data"}
                </span>
              )}
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

          {/* ── Pet Policy ──────────────────────────────────── */}
          {l.pet_policy && (
            <>
              <div className="flex items-center gap-2">
                <span className="text-base">🐾</span>
                <span className="text-sm text-zinc-300">{l.pet_policy}</span>
              </div>
              <div className="border-t border-zinc-800" />
            </>
          )}

          {/* ── Transit Stations with Route Badges ──────────── */}
          {l.transit_stations && l.transit_stations.length > 0 && (
            <>
              <div className="space-y-2">
                <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                  🚇 Nearby Transit
                </h3>
                <div className="space-y-2">
                  {l.transit_stations.map((station, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <div className="flex items-center gap-1 shrink-0">
                        {station.routes.map((route, j) => (
                          <span
                            key={j}
                            className="inline-flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-bold text-white"
                            style={{ backgroundColor: mtaRouteColor(route) }}
                          >
                            {route.length <= 2 ? route : route.charAt(0)}
                          </span>
                        ))}
                      </div>
                      <span className="text-sm text-zinc-300 flex-1 truncate">
                        {station.name}
                      </span>
                      <span className="text-xs text-zinc-500 shrink-0">
                        {station.distance_m < 200
                          ? "< 200m"
                          : station.distance_m < 1000
                            ? `${station.distance_m}m`
                            : `${(station.distance_m / 1609).toFixed(1)} mi`}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
              <div className="border-t border-zinc-800" />
            </>
          )}

          {/* ── Explore / Nearby POIs ───────────────────────── */}
          {l.nearby_pois && l.nearby_pois.length > 0 && (
            <>
              <div className="space-y-2">
                <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                  📍 Explore {l.neighborhood}
                </h3>
                <div className="space-y-1.5">
                  {l.nearby_pois.map((p, i) => (
                    <div key={i} className="flex items-center gap-2 text-sm">
                      <span className="shrink-0">{poiIcon(p.category)}</span>
                      <span className="text-zinc-300 flex-1">{p.name}</span>
                      <span className="text-xs text-zinc-500 shrink-0">
                        {p.distance_m === 0 ? "nearby" : p.distance_m < 1000 ? `${p.distance_m}m` : `${(p.distance_m / 1609).toFixed(1)} mi`}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
              <div className="border-t border-zinc-800" />
            </>
          )}

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

          {/* ── Categorized Amenities ───────────────────────── */}
          {l.categorized_amenities && (
            (() => {
              const cats = l.categorized_amenities;
              const sections = [
                { key: "unit_features", label: "Unit Features", icon: "🏠", items: cats.unit_features },
                { key: "services", label: "Services & Facilities", icon: "🛎️", items: cats.services },
                { key: "wellness", label: "Wellness & Recreation", icon: "🏋️", items: cats.wellness },
                { key: "outdoor", label: "Shared Outdoor Space", icon: "🌿", items: cats.outdoor },
                { key: "convenience", label: "Convenience", icon: "🚗", items: cats.convenience },
              ].filter((s) => s.items.length > 0);

              // Also show uncategorized amenities
              const allCategorized = new Set([
                ...cats.services, ...cats.wellness, ...cats.outdoor,
                ...cats.convenience, ...cats.unit_features,
              ]);
              const uncategorized = listing.amenities.filter((a) => !allCategorized.has(a));

              if (sections.length === 0 && uncategorized.length === 0) return null;

              return (
                <>
                  <div className="space-y-3">
                    <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                      Amenities
                    </h3>
                    {sections.map((section) => (
                      <div key={section.key} className="space-y-1">
                        <p className="text-xs text-zinc-500 font-medium">
                          {section.icon} {section.label}
                        </p>
                        <div className="flex flex-wrap gap-1.5">
                          {section.items.map((a, i) => (
                            <span
                              key={i}
                              className="px-2 py-0.5 rounded-full text-xs font-medium bg-zinc-800 text-zinc-300 border border-zinc-700"
                            >
                              {a}
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                    {uncategorized.length > 0 && (
                      <div className="space-y-1">
                        <p className="text-xs text-zinc-500 font-medium">Other</p>
                        <div className="flex flex-wrap gap-1.5">
                          {uncategorized.map((a, i) => (
                            <span
                              key={i}
                              className="px-2 py-0.5 rounded-full text-xs font-medium bg-zinc-800 text-zinc-300 border border-zinc-700"
                            >
                              {a}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="border-t border-zinc-800" />
                </>
              );
            })()
          )}

          {/* Fallback flat amenities if no categorized amenities */}
          {(!l.categorized_amenities || (
            l.categorized_amenities.services.length === 0 &&
            l.categorized_amenities.wellness.length === 0 &&
            l.categorized_amenities.outdoor.length === 0 &&
            l.categorized_amenities.convenience.length === 0 &&
            l.categorized_amenities.unit_features.length === 0
          )) && listing.amenities && listing.amenities.length > 0 && (
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

          {/* ── About the Building ──────────────────────────── */}
          <div className="space-y-2">
            <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
              🏢 About the Building
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

          <div className="border-t border-zinc-800" />

          {/* ── About the Neighborhood ──────────────────────── */}
          {l.neighborhood_info?.description && (
            <>
              <div className="space-y-2">
                <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                  About {l.neighborhood}
                </h3>
                <p className="text-sm text-zinc-400 leading-relaxed">
                  {l.neighborhood_info.description}
                </p>
                {(l.neighborhood_info.median_rent_1br || l.neighborhood_info.median_rent_2br) && (
                  <div className="flex gap-4 mt-1">
                    {l.neighborhood_info.median_rent_1br && (
                      <div>
                        <p className="text-[10px] text-zinc-500 uppercase">Median 1BR Rent</p>
                        <p className="text-sm font-medium text-zinc-300">
                          {formatPrice(l.neighborhood_info.median_rent_1br)}
                        </p>
                      </div>
                    )}
                    {l.neighborhood_info.median_rent_2br && (
                      <div>
                        <p className="text-[10px] text-zinc-500 uppercase">Median 2BR Rent</p>
                        <p className="text-sm font-medium text-zinc-300">
                          {formatPrice(l.neighborhood_info.median_rent_2br)}
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </div>
              <div className="border-t border-zinc-800" />
            </>
          )}

          {/* ── Nearby Neighborhoods ────────────────────────── */}
          {l.nearby_neighborhoods && l.nearby_neighborhoods.length > 0 && (
            <>
              <div className="space-y-2">
                <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                  Nearby Neighborhoods
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {l.nearby_neighborhoods.map((n, i) => (
                    <span
                      key={i}
                      className="px-2.5 py-1 rounded-full text-xs font-medium bg-zinc-800 text-zinc-300 border border-zinc-700"
                    >
                      {n}
                    </span>
                  ))}
                </div>
              </div>
              <div className="border-t border-zinc-800" />
            </>
          )}

          {/* ── Similar Listings ────────────────────────────── */}
          {l.similar && l.similar.length > 0 && (
            <>
              <div className="space-y-3">
                <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                  Similar Listings
                </h3>
                <div className="space-y-2">
                  {l.similar.map((comp) => (
                    <ComparableCard key={comp.id} comp={comp} />
                  ))}
                </div>
              </div>
              <div className="border-t border-zinc-800" />
            </>
          )}

          {/* ── Also Consider ──────────────────────────────── */}
          {l.also_consider && l.also_consider.length > 0 && (
            <>
              <div className="space-y-3">
                <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
                  Also Consider
                </h3>
                <p className="text-xs text-zinc-500">
                  Similar profile but stronger in one area
                </p>
                <div className="space-y-2">
                  {l.also_consider.map((comp) => (
                    <ComparableCard key={comp.id} comp={comp} />
                  ))}
                </div>
              </div>
              <div className="border-t border-zinc-800" />
            </>
          )}

          {/* ── Listing Link ─────────────────────────────── */}
          {listing.url && (
            <div className="pb-6">
              <a
                href={listing.url}
                target="_blank"
                rel="noopener noreferrer"
                className="block text-center text-sm font-medium text-blue-400 hover:text-blue-300 py-2"
              >
                View listing →
              </a>
            </div>
          )}

          {!listing.url && <div className="pb-6" />}
        </div>
      </motion.div>
    </AnimatePresence>
  );
}

// ── Comparable listing mini-card ─────────────────────────────

function ComparableCard({ comp }: { comp: ComparableListing }) {
  const addToWatchlist = useStore((s) => s.addToWatchlist);

  return (
    <div className="flex items-center gap-3 p-2 rounded-lg bg-zinc-800/50 border border-zinc-800">
      {/* Photo */}
      {comp.photo ? (
        <img
          src={comp.photo}
          alt={comp.address}
          className="w-14 h-14 rounded-lg object-cover shrink-0"
        />
      ) : (
        <div className="w-14 h-14 rounded-lg bg-zinc-700 flex items-center justify-center shrink-0">
          <svg className="w-6 h-6 text-zinc-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
          </svg>
        </div>
      )}
      {/* Info */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-zinc-200 truncate">
          {formatPrice(comp.price)}/mo
          {comp.better_in && (
            <span className={clsx(
              "ml-1.5 text-[10px] font-bold px-1.5 py-0.5 rounded-full",
              "bg-blue-500/20 text-blue-400"
            )}>
              {groupIcon(comp.better_in)} Better {GROUP_LABELS[comp.better_in as ScoreGroupKey] ?? comp.better_in}
            </span>
          )}
        </p>
        <p className="text-xs text-zinc-400 truncate">
          {comp.address}{comp.unit ? `, ${comp.unit}` : ""}
        </p>
        <p className="text-xs text-zinc-500">
          {formatBeds(comp.beds)} · {comp.neighborhood}
          {comp.sqft ? ` · ${comp.sqft} sqft` : ""}
        </p>
      </div>
      {/* Score */}
      <div className="shrink-0">
        <ScoreBadge score={comp.composite_score} size="sm" />
      </div>
    </div>
  );
}
