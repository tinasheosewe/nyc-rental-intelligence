/**
 * TopBar — Global navigation bar.
 *
 * Warm cream theme with amber accent.
 * Desktop: logo + action icons (tabs moved to ListPanel).
 * Mobile: logo + tabs (pill nav) + action icons.
 */

"use client";

import { useStore } from "@/lib/store";
import type { QueueTab } from "@/lib/types";
import clsx from "clsx";

const TABS: { key: QueueTab; label: string }[] = [
  { key: "explore", label: "Explore" },
  { key: "watchlist", label: "Watchlist" },
  { key: "shortlist", label: "Shortlist" },
];

export default function TopBar() {
  const activeTab = useStore((s) => s.activeTab);
  const setActiveTab = useStore((s) => s.setActiveTab);
  const viewMode = useStore((s) => s.viewMode);
  const setViewMode = useStore((s) => s.setViewMode);
  const watchlist = useStore((s) => s.watchlist);
  const shortlist = useStore((s) => s.shortlist);
  const setFilterSheetOpen = useStore((s) => s.setFilterSheetOpen);
  const setSettingsOpen = useStore((s) => s.setSettingsOpen);
  const mapOpen = useStore((s) => s.mapOpen);
  const setMapOpen = useStore((s) => s.setMapOpen);

  const counts: Record<QueueTab, number> = {
    explore: 0,
    watchlist: watchlist.length,
    shortlist: shortlist.length,
  };

  return (
    <header className="sticky top-0 z-50 bg-white/90 backdrop-blur-md border-b border-[#E5E0D8]">
      <div className="flex items-center justify-between h-14 px-4 max-w-7xl mx-auto">
        {/* Logo */}
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-lg font-bold tracking-tight text-gray-900">
            RESIDE
          </span>
          <span className="w-2 h-2 rounded-full bg-amber-500 -mt-2" />
        </div>

        {/* Mobile-only: Queue tabs */}
        <nav className="flex items-center gap-1 bg-[#F3F0EB] rounded-full p-1 lg:hidden">
          {TABS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={clsx(
                "px-4 py-1.5 rounded-full text-sm font-medium transition-all duration-200",
                activeTab === key
                  ? "bg-white text-gray-900 shadow-sm"
                  : "text-gray-500 hover:text-gray-700",
              )}
            >
              {label}
              {counts[key] > 0 && (
                <span className="ml-1.5 text-xs text-gray-400">
                  {counts[key]}
                </span>
              )}
            </button>
          ))}
        </nav>

        {/* Action icons */}
        <div className="flex items-center gap-2 shrink-0">
          {/* Filter */}
          <button
            onClick={() => setFilterSheetOpen(true)}
            className="p-2 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-[#F3F0EB] transition-colors"
            title="Filters"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
            </svg>
          </button>

          {/* Settings */}
          <button
            onClick={() => setSettingsOpen(true)}
            className="p-2 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-[#F3F0EB] transition-colors"
            title="Preferences"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </button>

          {/* Map toggle — mobile only */}
          <button
            onClick={() => setMapOpen(!mapOpen)}
            className={clsx(
              "p-2 rounded-lg transition-colors lg:hidden",
              mapOpen
                ? "text-amber-600 bg-amber-500/10"
                : "text-gray-400 hover:text-gray-700 hover:bg-[#F3F0EB]",
            )}
            title="Map overlay"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
            </svg>
          </button>

          {/* View toggle (explore only, mobile only) */}
          {activeTab === "explore" && (
            <button
              onClick={() => setViewMode(viewMode === "feed" ? "scan" : "feed")}
              className="p-2 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-[#F3F0EB] transition-colors lg:hidden"
              title={viewMode === "feed" ? "Scan view" : "Feed view"}
            >
              {viewMode === "feed" ? (
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                </svg>
              ) : (
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              )}
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
