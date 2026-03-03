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
import { fetchNeighborhoods, fetchAmenities } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import clsx from "clsx";

const BED_OPTIONS = [
  { value: 0, label: "Studio" },
  { value: 1, label: "1" },
  { value: 2, label: "2" },
  { value: 3, label: "3+" },
];

const DATA_QUALITY_OPTIONS = [
  { value: null, label: "Any" },
  { value: "limited", label: "Exclude very limited" },
  { value: "full", label: "Good data only" },
] as const;

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
  const [allAmenities, setAllAmenities] = useState<string[]>([]);
  const [amSearch, setAmSearch] = useState("");

  // Fetch neighborhoods + amenities on mount
  useEffect(() => {
    fetchNeighborhoods().then(setAllNeighborhoods).catch(() => {});
    fetchAmenities().then(setAllAmenities).catch(() => {});
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
            className="fixed bottom-0 left-0 right-0 z-50 bg-white rounded-t-2xl border-t border-[#E5E0D8] max-h-[80vh] overflow-y-auto"
          >
            {/* Handle */}
            <div className="flex justify-center pt-3 pb-2">
              <div className="w-10 h-1 bg-gray-200 rounded-full" />
            </div>

            <div className="px-5 pb-6 space-y-6">
              <h2 className="text-lg font-semibold text-white">Filters</h2>

              {/* Price range */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-gray-400 uppercase tracking-wider">
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
                    className="flex-1 bg-[#F3F0EB] border border-[#E5E0D8] rounded-lg px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-amber-400"
                  />
                  <span className="text-gray-400">—</span>
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
                    className="flex-1 bg-[#F3F0EB] border border-[#E5E0D8] rounded-lg px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-amber-400"
                  />
                </div>
              </div>

              {/* Bedrooms */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-gray-400 uppercase tracking-wider">
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
                          ? "bg-white text-gray-900"
                          : "bg-[#F3F0EB] text-gray-500 hover:bg-gray-200",
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Neighborhoods */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-gray-400 uppercase tracking-wider">
                  Neighborhoods
                  {draft.neighborhoods.length > 0 && (
                    <span className="ml-1 text-gray-500">({draft.neighborhoods.length})</span>
                  )}
                </label>
                <input
                  type="text"
                  placeholder="Search neighborhoods…"
                  value={nbSearch}
                  onChange={(e) => setNbSearch(e.target.value)}
                  className="w-full bg-[#F3F0EB] border border-[#E5E0D8] rounded-lg px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-amber-400"
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
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-white text-gray-900 hover:bg-gray-100"
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
                <div className="max-h-36 overflow-y-auto rounded-lg bg-[#F3F0EB] border border-[#E5E0D8]">
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
                        className="block w-full text-left px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-200 transition-colors"
                      >
                        {nb}
                      </button>
                    ))}
                </div>
              </div>

              {/* Rent stabilized */}
              <div className="flex items-center justify-between">
                <label className="text-sm text-gray-600">
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
                    draft.rentStabilized ? "bg-green-500" : "bg-gray-200",
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
                <label className="text-xs font-medium text-gray-400 uppercase tracking-wider">
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
                  className="w-full bg-[#F3F0EB] border border-[#E5E0D8] rounded-lg px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-amber-400"
                />
              </div>

              {/* Available before */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-gray-400 uppercase tracking-wider">
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
                  className="w-full bg-[#F3F0EB] border border-[#E5E0D8] rounded-lg px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-amber-400"
                />
              </div>

              {/* Minimum score */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-gray-400 uppercase tracking-wider">
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
                <div className="flex justify-between text-xs text-gray-400">
                  <span>0</span>
                  <span className="text-white font-medium">
                    {draft.minScore ?? 0}
                  </span>
                  <span>100</span>
                </div>
              </div>

              {/* Amenities */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-gray-400 uppercase tracking-wider">
                  Amenities
                  {draft.amenities.length > 0 && (
                    <span className="ml-1 text-gray-500">({draft.amenities.length})</span>
                  )}
                </label>
                <input
                  type="text"
                  placeholder="Search amenities\u2026"
                  value={amSearch}
                  onChange={(e) => setAmSearch(e.target.value)}
                  className="w-full bg-[#F3F0EB] border border-[#E5E0D8] rounded-lg px-3 py-2 text-sm text-gray-800 placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-amber-400"
                />
                {/* Selected chips */}
                {draft.amenities.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {draft.amenities.map((am) => (
                      <button
                        key={am}
                        onClick={() =>
                          setDraft((d) => ({
                            ...d,
                            amenities: d.amenities.filter((a) => a !== am),
                          }))
                        }
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-white text-gray-900 hover:bg-gray-100"
                      >
                        {am}
                        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </button>
                    ))}
                  </div>
                )}
                {/* Dropdown */}
                <div className="max-h-36 overflow-y-auto rounded-lg bg-[#F3F0EB] border border-[#E5E0D8]">
                  {allAmenities
                    .filter(
                      (am) =>
                        am.toLowerCase().includes(amSearch.toLowerCase()) &&
                        !draft.amenities.includes(am),
                    )
                    .map((am) => (
                      <button
                        key={am}
                        onClick={() =>
                          setDraft((d) => ({
                            ...d,
                            amenities: [...d.amenities, am],
                          }))
                        }
                        className="block w-full text-left px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-200 transition-colors"
                      >
                        {am}
                      </button>
                    ))}
                </div>
              </div>

              {/* Data availability */}
              <div className="space-y-2">
                <label className="text-xs font-medium text-gray-400 uppercase tracking-wider">
                  Data Availability
                </label>
                <div className="flex items-center gap-2">
                  {DATA_QUALITY_OPTIONS.map(({ value, label }) => (
                    <button
                      key={label}
                      onClick={() =>
                        setDraft((d) => ({ ...d, minDataQuality: value }))
                      }
                      className={clsx(
                        "px-4 py-2 rounded-lg text-sm font-medium transition-all",
                        draft.minDataQuality === value
                          ? "bg-white text-gray-900"
                          : "bg-[#F3F0EB] text-gray-500 hover:bg-gray-200",
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-3 pt-2">
                <button
                  onClick={handleReset}
                  className="flex-1 py-2.5 rounded-xl text-sm font-medium text-gray-500 bg-[#F3F0EB] hover:bg-gray-200"
                >
                  Reset
                </button>
                <button
                  onClick={handleApply}
                  className="flex-1 py-2.5 rounded-xl text-sm font-medium text-gray-900 bg-white hover:bg-gray-100"
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
