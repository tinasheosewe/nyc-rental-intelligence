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
import type {
  Listing,
  QueueTab,
  ViewMode,
  FilterState,
  ScoreGroupKey,
} from "./types";
import { DEFAULT_FILTERS, DEFAULT_GROUP_PRIORITIES } from "./types";
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
  expandedQueueId: string | null;
  setExpandedQueueId: (id: string | null) => void;

  // Preferences
  priorities: ScoreGroupKey[];
  setPriorities: (p: ScoreGroupKey[]) => void;
  kidsMode: boolean;
  setKidsMode: (on: boolean) => void;
  settingsOpen: boolean;
  setSettingsOpen: (open: boolean) => void;
}

// ── Store ──────────────────────────────────────────────────────

export const useStore = create<AppState>((set, get) => ({
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
      const { filters, sortBy, viewMode, priorities, kidsMode } = get();
      const pageSize = viewMode === "feed" ? 10 : 24;
      const res = await fetchListings(filters, sortBy, 1, pageSize, priorities, kidsMode);
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
    const { isLoadingMore, hasMore, currentPage, filters, sortBy, listings, viewMode, priorities, kidsMode } = get();
    if (isLoadingMore || !hasMore) return;
    set({ isLoadingMore: true });
    try {
      const pageSize = viewMode === "feed" ? 10 : 24;
      const nextPage = currentPage + 1;
      const res = await fetchListings(filters, sortBy, nextPage, pageSize, priorities, kidsMode);
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
  expandedQueueId: null,
  setExpandedQueueId: (id) => set({ expandedQueueId: id }),

  // Preferences
  priorities: [...DEFAULT_GROUP_PRIORITIES],
  setPriorities: (p) => {
    set({ priorities: p });
    get().loadListings();
  },
  kidsMode: false,
  setKidsMode: (on) => {
    set({ kidsMode: on });
    get().loadListings();
  },
  settingsOpen: false,
  setSettingsOpen: (open) => set({ settingsOpen: open }),
}));
