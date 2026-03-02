/**
 * useUrlState — Syncs full app state with the browser URL.
 *
 * Every meaningful piece of configuration is encoded so that a URL
 * will drop you off exactly where you were:
 *
 *   Navigation:  ?tab=watchlist&view=feed&listing=<uuid>
 *   Sorting:     &sort=deal_score
 *   Filters:     &beds=0,1,2&min=1500&max=3000&hoods=Williamsburg,DUMBO
 *                &rs=1&score=60&sqft=400&before=2026-04-01
 *                &amenities=dishwasher,laundry&quality=good
 *   Preferences: &kids=1&pri=safety,value,building,neighborhood,access
 *
 * Defaults are omitted to keep URLs clean.
 *
 * Behaviour:
 *   - On mount: reads URL → hydrates entire store → loads listings once
 *   - On state changes: replaceState (no history bloat)
 *   - On intentional nav (scan→feed): pushState via pushListingUrl()
 *   - On popstate (back/forward): reads URL → hydrates → reloads
 */

"use client";

import { useEffect, useRef } from "react";
import { useStore } from "./store";
import type { QueueTab, ViewMode, FilterState, ScoreGroupKey } from "./types";
import { DEFAULT_FILTERS, DEFAULT_GROUP_PRIORITIES } from "./types";

// ── Sentinel to prevent feedback loops ────────────────────────
let suppressPopstate = false;
let suppressUrlWrite = false;

// ── Default values (omitted from URL when matched) ────────────
const DEF_TAB: QueueTab = "explore";
const DEF_VIEW: ViewMode = "scan";
const DEF_SORT = "composite";
const DEF_QUALITY = "limited";
const DEF_PRI = DEFAULT_GROUP_PRIORITIES.join(",");

// ── Helpers ───────────────────────────────────────────────────

function arraysEqual<T>(a: T[], b: T[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

/** Serialize current store state to URLSearchParams */
function stateToParams(): URLSearchParams {
  const s = useStore.getState();
  const sp = new URLSearchParams();

  // Navigation
  if (s.activeTab !== DEF_TAB) sp.set("tab", s.activeTab);
  const view = s.activeTab === "explore" ? s.viewMode : undefined;
  if (view && view !== DEF_VIEW) sp.set("view", view);

  // Current listing (only in feed mode)
  if (s.activeTab === "explore" && s.viewMode === "feed") {
    const skipped = s.skipped;
    const visible = s.listings.filter((l) => !skipped.has(l.id));
    const idx = Math.min(s.feedIndex, visible.length - 1);
    const current = visible[idx];
    if (current) sp.set("listing", current.id);
  }

  // Sort
  if (s.sortBy !== DEF_SORT) sp.set("sort", s.sortBy);

  // Filters
  const f = s.filters;
  if (f.beds.length > 0) sp.set("beds", f.beds.join(","));
  if (f.minPrice != null) sp.set("min", String(f.minPrice));
  if (f.maxPrice != null) sp.set("max", String(f.maxPrice));
  if (f.neighborhoods.length > 0) sp.set("hoods", f.neighborhoods.join(","));
  if (f.rentStabilized != null) sp.set("rs", f.rentStabilized ? "1" : "0");
  if (f.minScore != null) sp.set("score", String(f.minScore));
  if (f.minSqft != null) sp.set("sqft", String(f.minSqft));
  if (f.availableBefore) sp.set("before", f.availableBefore);
  if (f.amenities.length > 0) sp.set("amenities", f.amenities.join(","));
  if ((f.minDataQuality ?? DEF_QUALITY) !== DEF_QUALITY)
    sp.set("quality", f.minDataQuality!);

  // Preferences
  if (s.kidsMode) sp.set("kids", "1");
  const pri = s.priorities.join(",");
  if (pri !== DEF_PRI) sp.set("pri", pri);

  return sp;
}

/** Deserialize URLSearchParams → partial store state */
function paramsToState(sp: URLSearchParams): {
  activeTab: QueueTab;
  viewMode: ViewMode;
  listing?: string;
  sortBy: string;
  filters: FilterState;
  kidsMode: boolean;
  priorities: ScoreGroupKey[];
} {
  const tab = (sp.get("tab") as QueueTab) || DEF_TAB;
  const view = (sp.get("view") as ViewMode) || DEF_VIEW;
  const listing = sp.get("listing") || undefined;
  const sortBy = sp.get("sort") || DEF_SORT;

  const beds = sp.get("beds")
    ? sp.get("beds")!.split(",").map(Number).filter((n) => !isNaN(n))
    : [];
  const minPrice = sp.has("min") ? Number(sp.get("min")) : null;
  const maxPrice = sp.has("max") ? Number(sp.get("max")) : null;
  const neighborhoods = sp.get("hoods")
    ? sp.get("hoods")!.split(",").filter(Boolean)
    : [];
  const rsRaw = sp.get("rs");
  const rentStabilized = rsRaw === "1" ? true : rsRaw === "0" ? false : null;
  const minScore = sp.has("score") ? Number(sp.get("score")) : null;
  const minSqft = sp.has("sqft") ? Number(sp.get("sqft")) : null;
  const availableBefore = sp.get("before") || null;
  const amenities = sp.get("amenities")
    ? sp.get("amenities")!.split(",").filter(Boolean)
    : [];
  const minDataQuality = sp.get("quality") || DEF_QUALITY;

  const kidsMode = sp.get("kids") === "1";
  const priorities = sp.get("pri")
    ? (sp.get("pri")!.split(",") as ScoreGroupKey[])
    : [...DEFAULT_GROUP_PRIORITIES];

  return {
    activeTab: tab,
    viewMode: view,
    listing,
    sortBy,
    filters: {
      beds,
      minPrice,
      maxPrice,
      neighborhoods,
      rentStabilized,
      minScore,
      minSqft,
      availableBefore,
      amenities,
      minDataQuality,
    },
    kidsMode,
    priorities,
  };
}

/** Write current state to URL via replaceState or pushState */
function syncToUrl(mode: "replace" | "push" = "replace") {
  if (typeof window === "undefined") return;
  const sp = stateToParams();
  const search = sp.toString();
  const url = window.location.pathname + (search ? `?${search}` : "");
  if (url === window.location.pathname + window.location.search) return;

  suppressPopstate = true;
  if (mode === "push") {
    window.history.pushState(null, "", url);
  } else {
    window.history.replaceState(null, "", url);
  }
  queueMicrotask(() => {
    suppressPopstate = false;
  });
}

/** Hydrate the store from URL params, then load listings once */
function hydrateFromUrl() {
  const sp = new URLSearchParams(window.location.search);
  const state = paramsToState(sp);

  // Batch-set into Zustand without triggering per-setter loadListings()
  suppressUrlWrite = true;
  useStore.setState({
    activeTab: state.activeTab,
    viewMode: state.viewMode,
    sortBy: state.sortBy,
    filters: state.filters,
    kidsMode: state.kidsMode,
    priorities: state.priorities,
  });
  suppressUrlWrite = false;

  // Store pending listing for resolution after data loads
  if (state.listing) {
    (window as any).__pendingListingId = state.listing;
  }

  // Load listings with the hydrated state
  useStore.getState().loadListings();
}

// ── Hook ──────────────────────────────────────────────────────

export function useUrlState() {
  const initialized = useRef(false);

  const listings = useStore((s) => s.listings);
  const feedIndex = useStore((s) => s.feedIndex);
  const activeTab = useStore((s) => s.activeTab);
  const viewMode = useStore((s) => s.viewMode);
  const sortBy = useStore((s) => s.sortBy);
  const filters = useStore((s) => s.filters);
  const kidsMode = useStore((s) => s.kidsMode);
  const priorities = useStore((s) => s.priorities);
  const isLoading = useStore((s) => s.isLoading);
  const setActiveTab = useStore((s) => s.setActiveTab);
  const setViewMode = useStore((s) => s.setViewMode);
  const setFeedIndex = useStore((s) => s.setFeedIndex);

  // ── On mount: hydrate everything from URL ───────────────────
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    hydrateFromUrl();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Resolve pending listing once data arrives ───────────────
  useEffect(() => {
    const pending = (window as any).__pendingListingId;
    if (!pending || isLoading || listings.length === 0) return;
    delete (window as any).__pendingListingId;

    const idx = listings.findIndex((l) => l.id === pending);
    if (idx >= 0) {
      setFeedIndex(idx);
      if (useStore.getState().viewMode !== "feed") setViewMode("feed");
    }
  }, [listings, isLoading]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Sync store → URL on any state change ────────────────────
  useEffect(() => {
    if (!initialized.current || suppressUrlWrite) return;
    syncToUrl("replace");
  }, [
    feedIndex,
    activeTab,
    viewMode,
    sortBy,
    filters,
    kidsMode,
    priorities,
    listings,
  ]);

  // ── Listen for back / forward ───────────────────────────────
  useEffect(() => {
    const handler = () => {
      if (suppressPopstate) return;
      const sp = new URLSearchParams(window.location.search);
      const state = paramsToState(sp);

      // Check if data-affecting params changed BEFORE setting state
      const prev = useStore.getState();
      const needsReload =
        prev.sortBy !== state.sortBy ||
        JSON.stringify(prev.filters) !== JSON.stringify(state.filters) ||
        prev.kidsMode !== state.kidsMode ||
        !arraysEqual(prev.priorities, state.priorities);

      // Batch-set state
      suppressUrlWrite = true;
      useStore.setState({
        activeTab: state.activeTab,
        viewMode: state.viewMode,
        sortBy: state.sortBy,
        filters: state.filters,
        kidsMode: state.kidsMode,
        priorities: state.priorities,
      });
      suppressUrlWrite = false;

      if (needsReload) {
        // Store pending listing for resolution after data loads
        if (state.listing) {
          (window as any).__pendingListingId = state.listing;
        }
        useStore.getState().loadListings();
      } else if (state.listing && prev.listings.length > 0) {
        // Same data, just navigate to the listing
        const idx = prev.listings.findIndex((l) => l.id === state.listing);
        if (idx >= 0) {
          useStore.setState({ feedIndex: idx });
          if (state.viewMode !== "feed")
            useStore.setState({ viewMode: "feed" });
        }
      }
    };

    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, []);
}

/**
 * Push a listing ID to the URL (for intentional navigation like
 * scan→feed clicks or queue card expansion). Preserves all
 * current filter/sort/preference state.
 */
export function pushListingUrl(listingId: string) {
  // Set listing view state first
  useStore.setState({ viewMode: "feed" });
  const listings = useStore.getState().listings;
  const skipped = useStore.getState().skipped;
  const visible = listings.filter((l) => !skipped.has(l.id));
  const idx = visible.findIndex((l) => l.id === listingId);
  if (idx >= 0) useStore.setState({ feedIndex: idx });

  syncToUrl("push");
}
