/**
 * FilterSheet — Slide-up filter panel.
 *
 * Contains price range, bedrooms, neighborhoods, rent-stabilized toggle,
 * and minimum score. Applies filters to re-fetch listings.
 */

"use client";

import { useState, useEffect } from "react";
import { useStore } from "@/lib/store";
import type { FilterState } from "@/lib/types";
import { fetchNeighborhoods } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import clsx from "clsx";

const BED_OPTIONS = [
  { value: 0, label: "Studio" },
  { value: 1, label: "1" },
  { value: 2, label: "2" },
  { value: 3, label: "3+" },
];

export default function FilterSheet() {
  const filterSheetOpen = useStore((s) => s.filterSheetOpen);
  const setFilterSheetOpen = useStore((s) => s.setFilterSheetOpen);
  const filters = useStore((s) => s.filters);
  const setFilters = useStore((s) => s.setFilters);
  const resetFilters = useStore((s) => s.resetFilters);

  // Local draft state (only commits on Apply)
  const [draft, setDraft] = useState<FilterState>(filters);
  const [allNeighborhoods, setAllNeighborhoods] = useState<string[]>([]);
  const [nbSearch, setNbSearch] = useState("");

  // Fetch neighborhoods on mount
  useEffect(() => {
    fetchNeighborhoods().then(setAllNeighborhoods).catch(() => {});
  }, []);

  // Sync draft when filters change externally
  useEffect(() => {
    setDraft(filters);
  }, [filters]);

  const toggleBed = (bed: number) => {
    setDraft((d) => ({
      ...d,
      beds: d.beds.includes(bed) ? d.beds.filter((b) => b !== bed) : [...d.beds, bed],
    }));
  };

  const handleApply = () => {
    setFilters(draft);
    setFilterSheetOpen(false);
  };

  const handleReset = () => {
    resetFilters();
    setFilterSheetOpen(false);
  };

  return (
    <AnimatePresence>
      {filterSheetOpen && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
            onClick={() => setFilterSheetOpen(false)}
          />

          {/* Sheet */}
          <motion.div
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={{ type: "spring", damping: 25, stiffness: 300 }}
            className="fixed bottom-0 left-0 right-0 z-50 bg-zinc-900 rounded-t-2xl border-t border-zinc-700 max-h-[80vh] overflow-y-auto"
          >
            {/* Handle */}
            <div className="flex justify-center pt-3 pb-2">
              <div className="w-10 h-1 bg-zinc-700 rounded-full" />
            </div>

            <div className="px-5 pb-6 space-y-6">
              <h2 className="text-lg font-semibold text-white">Filters</h2>

              {/* Price range */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-zinc-500 uppercase tracking-wider">
                  Price Range
                </label>
                <div className="flex items-center gap-3">
                  <input
                    type="number"
                    placeholder="Min"
                    value={draft.minPrice ?? ""}
                    onChange={(e) =>
                      setDraft((d) => ({
                        ...d,
                        minPrice: e.target.value ? Number(e.target.value) : null,
                      }))
                    }
                    className="flex-1 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-500"
                  />
                  <span className="text-zinc-600">—</span>
                  <input
                    type="number"
                    placeholder="Max"
                    value={draft.maxPrice ?? ""}
                    onChange={(e) =>
                      setDraft((d) => ({
                        ...d,
                        maxPrice: e.target.value ? Number(e.target.value) : null,
                      }))
                    }
                    className="flex-1 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-500"
                  />
                </div>
              </div>

              {/* Bedrooms */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-zinc-500 uppercase tracking-wider">
                  Bedrooms
                </label>
                <div className="flex items-center gap-2">
                  {BED_OPTIONS.map(({ value, label }) => (
                    <button
                      key={value}
                      onClick={() => toggleBed(value)}
                      className={clsx(
                        "px-4 py-2 rounded-lg text-sm font-medium transition-all",
                        draft.beds.includes(value)
                          ? "bg-white text-zinc-900"
                          : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700",
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Neighborhoods */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-zinc-500 uppercase tracking-wider">
                  Neighborhoods
                  {draft.neighborhoods.length > 0 && (
                    <span className="ml-1 text-zinc-400">({draft.neighborhoods.length})</span>
                  )}
                </label>
                <input
                  type="text"
                  placeholder="Search neighborhoods…"
                  value={nbSearch}
                  onChange={(e) => setNbSearch(e.target.value)}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-500"
                />
                {/* Selected chips */}
                {draft.neighborhoods.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {draft.neighborhoods.map((nb) => (
                      <button
                        key={nb}
                        onClick={() =>
                          setDraft((d) => ({
                            ...d,
                            neighborhoods: d.neighborhoods.filter((n) => n !== nb),
                          }))
                        }
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-white text-zinc-900 hover:bg-zinc-200"
                      >
                        {nb}
                        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </button>
                    ))}
                  </div>
                )}
                {/* Dropdown */}
                <div className="max-h-36 overflow-y-auto rounded-lg bg-zinc-800 border border-zinc-700">
                  {allNeighborhoods
                    .filter(
                      (nb) =>
                        nb.toLowerCase().includes(nbSearch.toLowerCase()) &&
                        !draft.neighborhoods.includes(nb),
                    )
                    .map((nb) => (
                      <button
                        key={nb}
                        onClick={() =>
                          setDraft((d) => ({
                            ...d,
                            neighborhoods: [...d.neighborhoods, nb],
                          }))
                        }
                        className="block w-full text-left px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-700 transition-colors"
                      >
                        {nb}
                      </button>
                    ))}
                </div>
              </div>

              {/* Rent stabilized */}
              <div className="flex items-center justify-between">
                <label className="text-sm text-zinc-300">
                  Rent-stabilized only
                </label>
                <button
                  onClick={() =>
                    setDraft((d) => ({
                      ...d,
                      rentStabilized: d.rentStabilized ? null : true,
                    }))
                  }
                  className={clsx(
                    "w-11 h-6 rounded-full transition-colors relative",
                    draft.rentStabilized ? "bg-green-500" : "bg-zinc-700",
                  )}
                >
                  <div
                    className={clsx(
                      "w-4 h-4 rounded-full bg-white absolute top-1 transition-transform",
                      draft.rentStabilized ? "translate-x-6" : "translate-x-1",
                    )}
                  />
                </button>
              </div>

              {/* Min sqft */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-zinc-500 uppercase tracking-wider">
                  Minimum Sqft
                </label>
                <input
                  type="number"
                  placeholder="e.g. 500"
                  value={draft.minSqft ?? ""}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      minSqft: e.target.value ? Number(e.target.value) : null,
                    }))
                  }
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-500"
                />
              </div>

              {/* Available before */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-zinc-500 uppercase tracking-wider">
                  Available By
                </label>
                <input
                  type="date"
                  value={draft.availableBefore ?? ""}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      availableBefore: e.target.value || null,
                    }))
                  }
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-500"
                />
              </div>

              {/* Minimum score */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-zinc-500 uppercase tracking-wider">
                  Minimum Composite Score
                </label>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={draft.minScore ?? 0}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      minScore: Number(e.target.value) || null,
                    }))
                  }
                  className="w-full accent-white"
                />
                <div className="flex justify-between text-xs text-zinc-500">
                  <span>0</span>
                  <span className="text-white font-medium">
                    {draft.minScore ?? 0}
                  </span>
                  <span>100</span>
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-3 pt-2">
                <button
                  onClick={handleReset}
                  className="flex-1 py-2.5 rounded-xl text-sm font-medium text-zinc-400 bg-zinc-800 hover:bg-zinc-700"
                >
                  Reset
                </button>
                <button
                  onClick={handleApply}
                  className="flex-1 py-2.5 rounded-xl text-sm font-medium text-zinc-900 bg-white hover:bg-zinc-200"
                >
                  Apply
                </button>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
