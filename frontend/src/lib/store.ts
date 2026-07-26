/**
 * Global application store (Zustand).
 *
 * Manages:
 *   - Active queue tab (explore / watchlist / shortlist)
 *   - Explore view mode (feed / scan)
 *   - Explore feed index (current card position)
 *   - Listings data (fetched from API)
 *   - Watchlist & Shortlist queues (user-curated)
 *   - Sort dimension
 *   - Filters
 *   - Compare selection
 *   - Map overlay toggle
 *   - Priority ranking
 */

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type {
  Listing,
  QueueTab,
  ViewMode,
  FilterState,
  ScoreGroupKey,
  ScoreDimension,
} from "./types";
import { DEFAULT_FILTERS, DEFAULT_GROUP_PRIORITIES, boostsToPriorities } from "./types";
import { fetchListings } from "./api";

// ── State shape ────────────────────────────────────────────────

interface AppState {
  // Navigation
  activeTab: QueueTab;
  setActiveTab: (tab: QueueTab) => void;

  // Explore
  viewMode: ViewMode;
  setViewMode: (mode: ViewMode) => void;
  feedIndex: number;
  setFeedIndex: (idx: number) => void;

  // Data
  listings: Listing[];
  totalListings: number;
  isLoading: boolean;
  isLoadingMore: boolean;
  currentPage: number;
  hasMore: boolean;
  loadListings: () => Promise<void>;
  loadMore: () => Promise<void>;

  // Queues
  watchlist: Listing[];
  shortlist: Listing[];
  skipped: Set<string>;
  addToWatchlist: (listing: Listing) => void;
  addToShortlist: (listing: Listing) => void;
  removeFromWatchlist: (id: string) => void;
  removeFromShortlist: (id: string) => void;
  moveToShortlist: (id: string) => void;
  moveToWatchlist: (id: string) => void;
  skipListing: (id: string) => void;
  isInWatchlist: (id: string) => boolean;
  isInShortlist: (id: string) => boolean;

  // Sorting
  sortBy: string;
  setSortBy: (sort: string) => void;

  // Filters
  filters: FilterState;
  setFilters: (filters: FilterState) => void;
  resetFilters: () => void;
  filterSheetOpen: boolean;
  setFilterSheetOpen: (open: boolean) => void;

  // Compare
  compareIds: Set<string>;
  toggleCompareId: (id: string) => void;
  clearCompare: () => void;
  compareOpen: boolean;
  setCompareOpen: (open: boolean) => void;

  // Map
  mapOpen: boolean;
  setMapOpen: (open: boolean) => void;
  mapColorOverlay: ScoreDimension | null;
  setMapColorOverlay: (dim: ScoreDimension | null) => void;
  /** Heatmap ramp renormalized to the scores currently in view (vs citywide absolute). */
  heatmapRelative: boolean;
  setHeatmapRelative: (on: boolean) => void;
  expandedQueueId: string | null;
  setExpandedQueueId: (id: string | null) => void;

  // Detail panel (3-panel layout)
  selectedListingId: string | null;
  setSelectedListingId: (id: string | null) => void;

  // Preferences
  /** Groups the user boosted (×2 weight), max 2. Source of truth. */
  boosts: ScoreGroupKey[];
  /** Dimensions excluded from the composite entirely. */
  ignoredDims: ScoreDimension[];
  /** Full 5-group ordering DERIVED from boosts (boosted first, then
   *  default order). Read-only display state for rings/pills/ordering. */
  priorities: ScoreGroupKey[];
  /** Apply boost + ignore selections in one shot (single reload). */
  setScoringPrefs: (boosts: ScoreGroupKey[], ignoredDims: ScoreDimension[]) => void;
  kidsMode: boolean;
  setKidsMode: (on: boolean) => void;
  settingsOpen: boolean;
  setSettingsOpen: (open: boolean) => void;
}

// ── Persistence helpers ────────────────────────────────────────
// Sets are not JSON-serializable, so persisted Sets are encoded as
// { __set: [...] } on write and revived back to Sets on read.

interface SerializedSet {
  __set: string[];
}

function isSerializedSet(value: unknown): value is SerializedSet {
  return (
    typeof value === "object" &&
    value !== null &&
    Array.isArray((value as SerializedSet).__set)
  );
}

// ── Store ──────────────────────────────────────────────────────

export const useStore = create<AppState>()(
  persist(
    (set, get) => ({
  // Navigation
  activeTab: "explore",
  setActiveTab: (tab) => set({ activeTab: tab, expandedQueueId: null }),

  // Explore
  viewMode: "scan",
  setViewMode: (mode) => set({ viewMode: mode }),
  feedIndex: 0,
  setFeedIndex: (idx) => set({ feedIndex: idx }),

  // Data — paginated lazy loading
  // Feed mode fetches 10/page, scan mode 24/page.
  // loadListings() resets to page 1; loadMore() appends the next page.

  listings: [],
  totalListings: 0,
  isLoading: false,
  isLoadingMore: false,
  currentPage: 0,
  hasMore: true,

  loadListings: async () => {
    set({ isLoading: true, listings: [], currentPage: 0, hasMore: true, feedIndex: 0 });
    try {
      const { filters, sortBy, viewMode, boosts, ignoredDims, kidsMode } = get();
      const pageSize = viewMode === "feed" ? 10 : 24;
      // Backward compat: the backend maps the top-2 "priorities" to boosts.
      const res = await fetchListings(filters, sortBy, 1, pageSize, boosts, kidsMode, ignoredDims);
      set({
        listings: res.listings,
        totalListings: res.total,
        isLoading: false,
        currentPage: 1,
        hasMore: res.listings.length < res.total,
      });
    } catch (err) {
      console.error("Failed to load listings:", err);
      set({ isLoading: false });
    }
  },

  loadMore: async () => {
    const { isLoadingMore, hasMore, currentPage, filters, sortBy, listings, viewMode, boosts, ignoredDims, kidsMode } = get();
    if (isLoadingMore || !hasMore) return;
    set({ isLoadingMore: true });
    try {
      const pageSize = viewMode === "feed" ? 10 : 24;
      const nextPage = currentPage + 1;
      const res = await fetchListings(filters, sortBy, nextPage, pageSize, boosts, kidsMode, ignoredDims);
      const merged = [...listings, ...res.listings];
      set({
        listings: merged,
        totalListings: res.total,
        isLoadingMore: false,
        currentPage: nextPage,
        hasMore: merged.length < res.total,
      });
    } catch (err) {
      console.error("Failed to load more:", err);
      set({ isLoadingMore: false });
    }
  },

  // Queues
  watchlist: [],
  shortlist: [],
  skipped: new Set(),

  addToWatchlist: (listing) =>
    set((s) => {
      if (s.watchlist.some((l) => l.id === listing.id)) return s;
      return { watchlist: [...s.watchlist, listing] };
    }),

  addToShortlist: (listing) =>
    set((s) => {
      if (s.shortlist.some((l) => l.id === listing.id)) return s;
      return {
        shortlist: [...s.shortlist, listing],
        // Also remove from watchlist if present
        watchlist: s.watchlist.filter((l) => l.id !== listing.id),
      };
    }),

  removeFromWatchlist: (id) =>
    set((s) => ({
      watchlist: s.watchlist.filter((l) => l.id !== id),
    })),

  removeFromShortlist: (id) =>
    set((s) => ({
      shortlist: s.shortlist.filter((l) => l.id !== id),
    })),

  moveToShortlist: (id) =>
    set((s) => {
      const listing = s.watchlist.find((l) => l.id === id);
      if (!listing) return s;
      return {
        watchlist: s.watchlist.filter((l) => l.id !== id),
        shortlist: [...s.shortlist, listing],
      };
    }),

  moveToWatchlist: (id) =>
    set((s) => {
      const listing = s.shortlist.find((l) => l.id === id);
      if (!listing) return s;
      return {
        shortlist: s.shortlist.filter((l) => l.id !== id),
        watchlist: [...s.watchlist, listing],
      };
    }),

  skipListing: (id) =>
    set((s) => {
      const next = new Set(s.skipped);
      next.add(id);
      return { skipped: next };
    }),

  isInWatchlist: (id) => get().watchlist.some((l) => l.id === id),
  isInShortlist: (id) => get().shortlist.some((l) => l.id === id),

  // Sorting
  sortBy: "composite",
  setSortBy: (sort) => {
    set({ sortBy: sort });
    get().loadListings();
  },

  // Filters
  filters: DEFAULT_FILTERS,
  setFilters: (filters) => {
    set({ filters });
    get().loadListings();
  },
  resetFilters: () => {
    set({ filters: DEFAULT_FILTERS });
    get().loadListings();
  },
  filterSheetOpen: false,
  setFilterSheetOpen: (open) => set({ filterSheetOpen: open }),

  // Compare
  compareIds: new Set(),
  toggleCompareId: (id) =>
    set((s) => {
      const next = new Set(s.compareIds);
      if (next.has(id)) next.delete(id);
      else if (next.size < 5) next.add(id);
      return { compareIds: next };
    }),
  clearCompare: () => set({ compareIds: new Set() }),
  compareOpen: false,
  setCompareOpen: (open) => set({ compareOpen: open }),

  // Map
  mapOpen: false,
  setMapOpen: (open) => set({ mapOpen: open }),
  mapColorOverlay: null,
  setMapColorOverlay: (dim) => set({ mapColorOverlay: dim }),
  heatmapRelative: false,
  setHeatmapRelative: (on) => set({ heatmapRelative: on }),
  expandedQueueId: null,
  setExpandedQueueId: (id) => set({ expandedQueueId: id }),

  // Detail panel (3-panel layout)
  selectedListingId: null,
  setSelectedListingId: (id) => set({ selectedListingId: id }),

  // Preferences
  boosts: [],
  ignoredDims: [],
  priorities: [...DEFAULT_GROUP_PRIORITIES],
  setScoringPrefs: (boosts, ignoredDims) => {
    const b = boosts.slice(0, 2);
    set({ boosts: b, ignoredDims, priorities: boostsToPriorities(b) });
    get().loadListings();
  },
  kidsMode: false,
  setKidsMode: (on) => {
    set({ kidsMode: on });
    get().loadListings();
  },
  settingsOpen: false,
  setSettingsOpen: (open) => set({ settingsOpen: open }),
    }),
    {
      name: "apthunt-user-v1",
      // Persist ONLY user-curated data (queues + scoring preferences).
      // Listings/filters/sort and all transient UI state (open sheets,
      // indices, loading flags) are deliberately excluded and rebuilt
      // fresh on each load.
      partialize: (s) => ({
        watchlist: s.watchlist,
        shortlist: s.shortlist,
        skipped: s.skipped,
        compareIds: s.compareIds,
        boosts: s.boosts,
        ignoredDims: s.ignoredDims,
      }),
      // `priorities` is derived from boosts, not persisted — rebuild it
      // after rehydration so display ordering matches the stored boosts.
      merge: (persisted, current) => {
        const merged = { ...current, ...(persisted as Partial<AppState>) };
        return { ...merged, priorities: boostsToPriorities(merged.boosts ?? []) };
      },
      // createJSONStorage defers the localStorage access until it is
      // actually used (and no-ops when unavailable), so this stays
      // SSR-safe under Next.js.
      storage: createJSONStorage(() => localStorage, {
        replacer: (_key, value) =>
          value instanceof Set ? { __set: Array.from(value) as string[] } : value,
        reviver: (_key, value) =>
          isSerializedSet(value) ? new Set(value.__set) : value,
      }),
    },
  ),
);
