/**
 * DetailPanel — Right panel in the 3-panel desktop layout.
 *
 * Shows full listing details for the selected listing:
 *   - Photo carousel
 *   - Address, price, stats
 *   - Score rings
 *   - Score breakdown (collapsible groups)
 *   - Flags / insights
 *   - Transit, POIs, amenities
 *   - Building info, neighborhood, comparables
 *   - Triage action buttons (Save / Shortlist / Skip)
 */

"use client";

import { useState, useEffect } from "react";
import { useStore } from "@/lib/store";
import { fetchListing } from "@/lib/api";
import type {
  Listing,
  ScoreDimension,
  ScoreGroupKey,
  ComparableListing,
} from "@/lib/types";
import {
  DIMENSION_LABELS,
  DIMENSION_BREAKOUT,
  SCORE_GROUP_KEYS,
  GROUP_LABELS,
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
import ScoreRings from "@/components/ui/ScoreRings";
import ScoreBar from "@/components/ui/ScoreBar";
import ScoreBadge from "@/components/ui/ScoreBadge";
import FlagList from "@/components/ui/FlagList";
import PhotoCarousel from "@/components/ui/PhotoCarousel";
import clsx from "clsx";

export default function DetailPanel() {
  const selectedListingId = useStore((s) => s.selectedListingId);
  const setSelectedListingId = useStore((s) => s.setSelectedListingId);
  const listings = useStore((s) => s.listings);
  const watchlist = useStore((s) => s.watchlist);
  const shortlist = useStore((s) => s.shortlist);
  const priorities = useStore((s) => s.priorities);
  const kidsMode = useStore((s) => s.kidsMode);
  const addToWatchlist = useStore((s) => s.addToWatchlist);
  const addToShortlist = useStore((s) => s.addToShortlist);
  const skipListing = useStore((s) => s.skipListing);

  // Find listing in any list
  const listing = [...listings, ...watchlist, ...shortlist].find(
    (l) => l.id === selectedListingId,
  );

  // Lazy-load detail data
  const [detail, setDetail] = useState<Listing | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    if (!selectedListingId) { setDetail(null); return; }
    let cancelled = false;
    setDetail(null);
    setDetailLoading(true);
    fetchListing(selectedListingId)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [selectedListingId]);

  const l = detail ?? listing;

  // Expanded score groups
  const [expandedGroups, setExpandedGroups] = useState<Set<ScoreGroupKey>>(new Set());
  const toggleGroup = (key: ScoreGroupKey) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  // Queue membership
  const inWatchlist = watchlist.some((w) => w.id === selectedListingId);
  const inShortlist = shortlist.some((s) => s.id === selectedListingId);

  // Groups ordered by priorities
  const effectiveGroupByKey = getEffectiveGroups(kidsMode).reduce(
    (acc, g) => ({ ...acc, [g.key]: g }),
    {} as Record<string, (typeof import("@/lib/types"))["SCORE_GROUPS"][number]>,
  );
  const orderedGroups = priorities.map((gk) => effectiveGroupByKey[gk]).filter(Boolean);

  if (!listing || !l) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-white border-l border-[#E5E0D8] text-gray-400 px-8">
        <svg className="w-16 h-16 text-gray-200 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
        </svg>
        <p className="text-sm font-medium">Select a listing</p>
        <p className="text-xs mt-1">Click a card on the left to view details</p>
      </div>
    );
  }

  const hasPhoto = listing.photos.length > 0;

  return (
    <div className="flex flex-col h-full bg-white border-l border-[#E5E0D8] overflow-hidden">
      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto">
        {/* Photo carousel */}
        <div className="relative w-full shrink-0">
          {hasPhoto ? (
            <PhotoCarousel photos={listing.photos} alt={listing.address} aspect="aspect-[16/10]" />
          ) : (
            <div className="w-full aspect-[16/10] bg-gray-100 flex items-center justify-center text-gray-300">
              <svg className="w-16 h-16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 22V12h6v10" />
              </svg>
            </div>
          )}
          {listing.no_fee && (
            <span className="absolute top-3 left-3 bg-emerald-500 text-white text-xs font-bold px-2 py-1 rounded z-10">
              NO FEE
            </span>
          )}
          {/* Close button */}
          <button
            onClick={() => setSelectedListingId(null)}
            className="absolute top-3 right-3 z-10 w-8 h-8 rounded-full bg-white/80 backdrop-blur flex items-center justify-center text-gray-500 hover:text-gray-800 shadow"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="px-5 pt-4 pb-6 space-y-5">
          {/* Address + price */}
          <div>
            <h2 className="text-xl font-semibold text-gray-900">
              {formatPrice(listing.price)}/mo
            </h2>
            <p className="text-sm text-gray-500 mt-0.5">
              {formatBeds(listing.beds)} · {formatBaths(listing.baths)}
              {listing.sqft ? ` · ${listing.sqft} sqft` : ""}
              {listing.sqft && listing.price
                ? ` · $${(listing.price / listing.sqft).toFixed(2)}/sqft`
                : ""}
            </p>
            <p className="text-sm text-gray-600 mt-0.5">
              {listing.address}{listing.unit ? `, ${listing.unit}` : ""}
            </p>
            <p className="text-xs text-gray-400 mt-0.5">
              {listing.neighborhood}, {listing.borough}
              {listing.days_on_market != null && (
                <span className="ml-2">· {formatDaysOnMarket(listing.days_on_market)}</span>
              )}
            </p>
            {listing.data_quality && (
              <span
                className={clsx(
                  "inline-block mt-1 text-[10px] font-medium px-2 py-0.5 rounded-full",
                  listing.data_quality === "very_limited"
                    ? "bg-red-50 text-red-500"
                    : "bg-amber-50 text-amber-600",
                )}
              >
                {listing.data_quality === "very_limited" ? "Very limited data" : "Limited data"}
              </span>
            )}
          </div>

          {/* Score rings */}
          <div className="bg-[#FAF7F2] rounded-xl p-4">
            <ScoreRings scores={listing.scores} kidsMode={kidsMode} priorities={priorities} />
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => skipListing(listing.id)}
              className="flex-1 py-2 rounded-xl bg-gray-100 text-gray-500 font-medium text-sm hover:bg-gray-200 transition-colors"
            >
              Skip
            </button>
            <button
              onClick={() => addToWatchlist(listing)}
              disabled={inWatchlist || inShortlist}
              className={
                inWatchlist || inShortlist
                  ? "flex-1 py-2 rounded-xl bg-gray-100 text-gray-400 font-medium text-sm cursor-not-allowed"
                  : "flex-1 py-2 rounded-xl bg-amber-500 text-white font-medium text-sm hover:bg-amber-600 transition-colors"
              }
            >
              {inWatchlist ? "In Watchlist" : inShortlist ? "In Shortlist" : "Save"}
            </button>
            {!inShortlist && (
              <button
                onClick={() => addToShortlist(listing)}
                className="p-2 rounded-xl bg-amber-50 text-amber-600 hover:bg-amber-100 transition-colors"
                title="Add to Shortlist"
              >
                ★
              </button>
            )}
          </div>

          <div className="border-t border-[#E5E0D8]" />

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
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-sky-50 text-sky-600">
                Rent Stabilized
              </span>
            )}
          </div>

          <div className="border-t border-[#E5E0D8]" />

          {/* Score breakdown */}
          <div className="space-y-1">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
              Score Breakdown
            </h3>
            {orderedGroups.map((group) => {
              const gs = getGroupScore(listing.scores, group.key, kidsMode);
              if (gs === null) return null;
              const isExpanded = expandedGroups.has(group.key);

              return (
                <div key={group.key} className="rounded-lg border border-[#E5E0D8] overflow-hidden">
                  <button
                    onClick={() => toggleGroup(group.key)}
                    className="w-full flex items-center gap-3 px-3 py-2.5 hover:bg-[#F3F0EB] transition-colors"
                  >
                    <span className="text-base">{group.icon}</span>
                    <span className="flex-1 text-left text-sm font-medium text-gray-700">
                      {group.label}
                    </span>
                    <span className={clsx("text-sm font-bold", scoreColor(gs))}>
                      {scoreLabel(gs)}
                    </span>
                    <span className="text-xs text-gray-400 w-4 text-center select-none">
                      {isExpanded ? "▾" : "▸"}
                    </span>
                  </button>

                  {isExpanded && (
                    <div className="px-3 pb-3 pt-1 space-y-2 border-t border-[#E5E0D8]">
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

          <div className="border-t border-[#E5E0D8]" />

          {/* Insights / Flags */}
          {listing.flags.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                Insights
              </h3>
              <FlagList flags={listing.flags} />
            </div>
          )}

          {listing.flags.length > 0 && <div className="border-t border-[#E5E0D8]" />}

          {/* Pet Policy */}
          {l.pet_policy && (
            <>
              <div className="flex items-center gap-2">
                <span className="text-base">🐾</span>
                <span className="text-sm text-gray-600">{l.pet_policy}</span>
              </div>
              <div className="border-t border-[#E5E0D8]" />
            </>
          )}

          {/* Transit */}
          {l.transit_stations && l.transit_stations.length > 0 && (
            <>
              <div className="space-y-2">
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
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
                      <span className="text-sm text-gray-600 flex-1 truncate">
                        {station.name}
                      </span>
                      <span className="text-xs text-gray-400 shrink-0">
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
              <div className="border-t border-[#E5E0D8]" />
            </>
          )}

          {/* POIs */}
          {l.nearby_pois && l.nearby_pois.length > 0 && (
            <>
              <div className="space-y-2">
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                  📍 Explore {l.neighborhood}
                </h3>
                <div className="space-y-1.5">
                  {l.nearby_pois.map((p, i) => (
                    <div key={i} className="flex items-center gap-2 text-sm">
                      <span className="shrink-0">{poiIcon(p.category)}</span>
                      <span className="text-gray-600 flex-1">{p.name}</span>
                      <span className="text-xs text-gray-400 shrink-0">
                        {p.distance_m === 0 ? "nearby" : p.distance_m < 1000 ? `${p.distance_m}m` : `${(p.distance_m / 1609).toFixed(1)} mi`}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
              <div className="border-t border-[#E5E0D8]" />
            </>
          )}

          {/* Description */}
          {listing.description && (
            <>
              <div className="space-y-2">
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                  Description
                </h3>
                <p className="text-sm text-gray-500 whitespace-pre-line leading-relaxed">
                  {listing.description}
                </p>
              </div>
              <div className="border-t border-[#E5E0D8]" />
            </>
          )}

          {/* Amenities */}
          {l.categorized_amenities && (() => {
            const cats = l.categorized_amenities;
            const sections = [
              { key: "unit_features", label: "Unit Features", icon: "🏠", items: cats.unit_features },
              { key: "services", label: "Services & Facilities", icon: "🛎️", items: cats.services },
              { key: "wellness", label: "Wellness & Recreation", icon: "🏋️", items: cats.wellness },
              { key: "outdoor", label: "Shared Outdoor Space", icon: "🌿", items: cats.outdoor },
              { key: "convenience", label: "Convenience", icon: "🚗", items: cats.convenience },
            ].filter((s) => s.items.length > 0);
            const allCategorized = new Set([
              ...cats.services, ...cats.wellness, ...cats.outdoor,
              ...cats.convenience, ...cats.unit_features,
            ]);
            const uncategorized = listing.amenities.filter((a) => !allCategorized.has(a));

            if (sections.length === 0 && uncategorized.length === 0) return null;
            return (
              <>
                <div className="space-y-3">
                  <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Amenities</h3>
                  {sections.map((section) => (
                    <div key={section.key} className="space-y-1">
                      <p className="text-xs text-gray-400 font-medium">{section.icon} {section.label}</p>
                      <div className="flex flex-wrap gap-1.5">
                        {section.items.map((a, i) => (
                          <span key={i} className="px-2 py-0.5 rounded-full text-xs font-medium bg-[#F3F0EB] text-gray-600 border border-[#E5E0D8]">
                            {a}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                  {uncategorized.length > 0 && (
                    <div className="space-y-1">
                      <p className="text-xs text-gray-400 font-medium">Other</p>
                      <div className="flex flex-wrap gap-1.5">
                        {uncategorized.map((a, i) => (
                          <span key={i} className="px-2 py-0.5 rounded-full text-xs font-medium bg-[#F3F0EB] text-gray-600 border border-[#E5E0D8]">
                            {a}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
                <div className="border-t border-[#E5E0D8]" />
              </>
            );
          })()}

          {/* Flat amenities fallback */}
          {(!l.categorized_amenities || (
            l.categorized_amenities.services.length === 0 &&
            l.categorized_amenities.wellness.length === 0 &&
            l.categorized_amenities.outdoor.length === 0 &&
            l.categorized_amenities.convenience.length === 0 &&
            l.categorized_amenities.unit_features.length === 0
          )) && listing.amenities && listing.amenities.length > 0 && (
            <>
              <div className="space-y-2">
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Amenities</h3>
                <div className="flex flex-wrap gap-1.5">
                  {listing.amenities.map((a, i) => (
                    <span key={i} className="px-2 py-0.5 rounded-full text-xs font-medium bg-[#F3F0EB] text-gray-600 border border-[#E5E0D8]">{a}</span>
                  ))}
                </div>
              </div>
              <div className="border-t border-[#E5E0D8]" />
            </>
          )}

          {/* Price History */}
          {listing.price_history && listing.price_history.length > 0 && (
            <>
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Price History</h3>
                  {listing.relist_count > 1 && (
                    <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-amber-50 text-amber-600">
                      Relisted {listing.relist_count}x
                    </span>
                  )}
                </div>
                <div className="space-y-1.5">
                  {listing.price_history.map((ph, i) => (
                    <div key={i} className="flex items-center justify-between text-sm">
                      <span className="text-gray-400">{ph.date}</span>
                      <span className="text-gray-600 font-medium">{ph.price}</span>
                      <span className="text-gray-400 text-xs truncate max-w-[140px]">{ph.event}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div className="border-t border-[#E5E0D8]" />
            </>
          )}

          {/* Building */}
          <div className="space-y-2">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">🏢 About the Building</h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              {listing.building.owner && (<><dt className="text-gray-400">Owner</dt><dd className="text-gray-600">{listing.building.owner}</dd></>)}
              {listing.building.year_built && (<><dt className="text-gray-400">Year Built</dt><dd className="text-gray-600">{listing.building.year_built}</dd></>)}
              {listing.building.total_units != null && listing.building.total_units > 0 && (<><dt className="text-gray-400">Units</dt><dd className="text-gray-600">{listing.building.total_units}</dd></>)}
              {listing.building.stories != null && listing.building.stories > 0 && (<><dt className="text-gray-400">Stories</dt><dd className="text-gray-600">{listing.building.stories}</dd></>)}
              <dt className="text-gray-400">Building Violations</dt><dd className="text-gray-600">{listing.building.open_violations} open</dd>
              <dt className="text-gray-400">HPD Complaints</dt><dd className="text-gray-600">{listing.building.hpd_complaints_12mo} (12mo)</dd>
            </dl>
          </div>

          <div className="border-t border-[#E5E0D8]" />

          {/* Neighborhood */}
          {l.neighborhood_info?.description && (
            <>
              <div className="space-y-2">
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">About {l.neighborhood}</h3>
                <p className="text-sm text-gray-500 leading-relaxed">{l.neighborhood_info.description}</p>
                {(l.neighborhood_info.median_rent_1br || l.neighborhood_info.median_rent_2br) && (
                  <div className="flex gap-4 mt-1">
                    {l.neighborhood_info.median_rent_1br && (
                      <div>
                        <p className="text-[10px] text-gray-400 uppercase">Median 1BR Rent</p>
                        <p className="text-sm font-medium text-gray-600">{formatPrice(l.neighborhood_info.median_rent_1br)}</p>
                      </div>
                    )}
                    {l.neighborhood_info.median_rent_2br && (
                      <div>
                        <p className="text-[10px] text-gray-400 uppercase">Median 2BR Rent</p>
                        <p className="text-sm font-medium text-gray-600">{formatPrice(l.neighborhood_info.median_rent_2br)}</p>
                      </div>
                    )}
                  </div>
                )}
              </div>
              <div className="border-t border-[#E5E0D8]" />
            </>
          )}

          {/* Similar Listings */}
          {l.similar && l.similar.length > 0 && (
            <>
              <div className="space-y-3">
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Similar Listings</h3>
                <div className="space-y-2">
                  {l.similar.map((comp) => (
                    <CompCard key={comp.id} comp={comp} />
                  ))}
                </div>
              </div>
              <div className="border-t border-[#E5E0D8]" />
            </>
          )}

          {/* Also Consider */}
          {l.also_consider && l.also_consider.length > 0 && (
            <>
              <div className="space-y-3">
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Also Consider</h3>
                <div className="space-y-2">
                  {l.also_consider.map((comp) => (
                    <CompCard key={comp.id} comp={comp} />
                  ))}
                </div>
              </div>
              <div className="border-t border-[#E5E0D8]" />
            </>
          )}

          {/* Listing link */}
          {listing.url && (
            <div className="pb-4">
              <a
                href={listing.url}
                target="_blank"
                rel="noopener noreferrer"
                className="block text-center text-sm font-medium text-amber-600 hover:text-amber-500 py-2"
              >
                View listing →
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Comparable mini-card ────────────────────────────────────

function CompCard({ comp }: { comp: ComparableListing }) {
  return (
    <div className="flex items-center gap-3 p-2 rounded-lg bg-[#FAF7F2] border border-[#E5E0D8]">
      {comp.photo ? (
        <img src={comp.photo} alt={comp.address} className="w-14 h-14 rounded-lg object-cover shrink-0" />
      ) : (
        <div className="w-14 h-14 rounded-lg bg-gray-100 flex items-center justify-center shrink-0">
          <svg className="w-6 h-6 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
          </svg>
        </div>
      )}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-700 truncate">
          {formatPrice(comp.price)}/mo
          {comp.better_in && (
            <span className="ml-1.5 text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-sky-50 text-sky-600">
              {groupIcon(comp.better_in)} Better {GROUP_LABELS[comp.better_in as ScoreGroupKey] ?? comp.better_in}
            </span>
          )}
        </p>
        <p className="text-xs text-gray-500 truncate">
          {comp.address}{comp.unit ? `, ${comp.unit}` : ""}
        </p>
        <p className="text-xs text-gray-400">
          {formatBeds(comp.beds)} · {comp.neighborhood}
          {comp.sqft ? ` · ${comp.sqft} sqft` : ""}
        </p>
      </div>
      <div className="shrink-0">
        <ScoreBadge score={comp.composite_score} size="sm" />
      </div>
    </div>
  );
}
