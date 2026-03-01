/**
 * useUrlState — Syncs app navigation state with the browser URL.
 *
 * Query params:
 *   ?listing=<id>    — currently visible listing (feed mode)
 *   &tab=<tab>       — active queue tab
 *   &view=<mode>     — feed or scan
 *
 * Behaviour:
 *   - On mount: reads URL → hydrates store (if listing param present, fetches it)
 *   - On feed navigation: replaceState (no history bloat)
 *   - On intentional navigation (scan→feed, queue expand): pushState
 *   - On popstate (back/forward): reads URL → updates store
 */

"use client";

import { useEffect, useRef, useCallback } from "react";
import { useStore } from "./store";
import { fetchListing } from "./api";
import type { QueueTab, ViewMode } from "./types";

// Sentinel to prevent feedback loops
let suppressPopstate = false;

function buildSearch(params: Record<string, string | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v && v !== defaultFor(k)) sp.set(k, v);
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

function defaultFor(key: string): string | undefined {
  if (key === "tab") return "explore";
  if (key === "view") return "feed";
  return undefined;
}

/** Read current state from the URL search params */
function readUrl(): { listing?: string; tab?: QueueTab; view?: ViewMode } {
  if (typeof window === "undefined") return {};
  const sp = new URLSearchParams(window.location.search);
  return {
    listing: sp.get("listing") ?? undefined,
    tab: (sp.get("tab") as QueueTab) ?? undefined,
    view: (sp.get("view") as ViewMode) ?? undefined,
  };
}

/** Write state to URL (replace or push) */
function writeUrl(
  params: { listing?: string; tab?: string; view?: string },
  mode: "replace" | "push" = "replace",
) {
  const search = buildSearch(params);
  const url = window.location.pathname + search;
  if (url === window.location.pathname + window.location.search) return;
  suppressPopstate = true;
  if (mode === "push") {
    window.history.pushState(null, "", url);
  } else {
    window.history.replaceState(null, "", url);
  }
  // Reset after microtask
  queueMicrotask(() => {
    suppressPopstate = false;
  });
}

export function useUrlState() {
  const initialized = useRef(false);

  const listings = useStore((s) => s.listings);
  const feedIndex = useStore((s) => s.feedIndex);
  const activeTab = useStore((s) => s.activeTab);
  const viewMode = useStore((s) => s.viewMode);
  const setActiveTab = useStore((s) => s.setActiveTab);
  const setViewMode = useStore((s) => s.setViewMode);
  const setFeedIndex = useStore((s) => s.setFeedIndex);
  const isLoading = useStore((s) => s.isLoading);

  // ── On mount: hydrate from URL ──────────────────────────────
  useEffect(() => {
    if (initialized.current) return;
    const { tab, view, listing } = readUrl();
    if (tab) setActiveTab(tab);
    if (view) setViewMode(view);

    if (listing) {
      // We need listings to be loaded first to find the index
      // Store the listing ID; we'll resolve it once data arrives
      (window as any).__pendingListingId = listing;
    }
    initialized.current = true;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Once listings load, resolve pending listing ID
  useEffect(() => {
    const pending = (window as any).__pendingListingId;
    if (!pending || isLoading || listings.length === 0) return;
    delete (window as any).__pendingListingId;

    const idx = listings.findIndex((l) => l.id === pending);
    if (idx >= 0) {
      setFeedIndex(idx);
      setViewMode("feed");
    }
    // If not found in current page, leave as-is (listing may be on a later page)
  }, [listings, isLoading]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Sync store → URL on state changes ───────────────────────
  useEffect(() => {
    if (!initialized.current) return;
    const skipped = useStore.getState().skipped;
    const visible = listings.filter((l) => !skipped.has(l.id));
    const currentIndex = Math.min(feedIndex, visible.length - 1);
    const current = visible[currentIndex];
    const listingId =
      activeTab === "explore" && viewMode === "feed" && current
        ? current.id
        : undefined;

    writeUrl({
      listing: listingId,
      tab: activeTab,
      view: activeTab === "explore" ? viewMode : undefined,
    });
  }, [feedIndex, activeTab, viewMode, listings]);

  // ── Listen for back/forward ─────────────────────────────────
  useEffect(() => {
    const handler = () => {
      if (suppressPopstate) return;
      const { tab, view, listing } = readUrl();
      if (tab) setActiveTab(tab);
      if (view) setViewMode(view);

      if (listing && listings.length > 0) {
        const idx = listings.findIndex((l) => l.id === listing);
        if (idx >= 0) {
          setFeedIndex(idx);
          if (view !== "feed") setViewMode("feed");
        }
      }
    };

    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, [listings, setActiveTab, setViewMode, setFeedIndex]);
}

/**
 * Push a listing ID to the URL (for intentional navigation like
 * scan→feed clicks or queue card expansion).
 */
export function pushListingUrl(listingId: string) {
  const tab = useStore.getState().activeTab;
  writeUrl({ listing: listingId, tab, view: "feed" }, "push");
}
