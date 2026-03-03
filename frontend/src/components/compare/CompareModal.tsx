/**
 * CompareModal — Full-screen compare overlay.
 *
 * Step 1: Metric picker (select score groups to compare).
 * Step 2: Three-tab comparison view (Table / Flags / Radar).
 */

"use client";

import { useState, useMemo, useEffect } from "react";
import dynamic from "next/dynamic";
import { useStore } from "@/lib/store";
import type { ScoreGroupKey, CompareTab } from "@/lib/types";
import { SCORE_GROUPS, GROUP_BY_KEY, getEffectiveGroups } from "@/lib/types";
import CompareTable from "./CompareTable";
import CompareFlags from "./CompareFlags";
import clsx from "clsx";
import { motion, AnimatePresence } from "framer-motion";

const CompareRadar = dynamic(() => import("./CompareRadar"), { ssr: false });

const TABS: { key: CompareTab; label: string }[] = [
  { key: "table", label: "Table" },
  { key: "flags", label: "Flags" },
  { key: "radar", label: "Radar" },
];

export default function CompareModal() {
  const compareOpen = useStore((s) => s.compareOpen);
  const setCompareOpen = useStore((s) => s.setCompareOpen);
  const compareIds = useStore((s) => s.compareIds);
  const clearCompare = useStore((s) => s.clearCompare);
  const watchlist = useStore((s) => s.watchlist);
  const shortlist = useStore((s) => s.shortlist);
  const priorities = useStore((s) => s.priorities);
  const kidsMode = useStore((s) => s.kidsMode);
  const effectiveGroups = getEffectiveGroups(kidsMode);

  const [step, setStep] = useState<"pick" | "compare">("pick");
  const [selectedGroups, setSelectedGroups] = useState<Set<ScoreGroupKey>>(
    new Set(priorities.slice(0, 4) as ScoreGroupKey[]),
  );
  const [activeTab, setActiveTab] = useState<CompareTab>("table");

  // Reset selections when modal opens or priorities change
  useEffect(() => {
    if (compareOpen) {
      setSelectedGroups(new Set(priorities.slice(0, 4) as ScoreGroupKey[]));
      setStep("pick");
    }
  }, [compareOpen, priorities]);

  // Gather listings from both queues
  const allQueued = useMemo(
    () => [...watchlist, ...shortlist],
    [watchlist, shortlist],
  );
  const listings = useMemo(
    () => allQueued.filter((l) => compareIds.has(l.id)),
    [allQueued, compareIds],
  );

  const toggleGroup = (gk: ScoreGroupKey) => {
    const next = new Set(selectedGroups);
    if (next.has(gk)) {
      if (next.size > 2) next.delete(gk);
    } else if (next.size < 5) {
      next.add(gk);
    }
    setSelectedGroups(next);
  };

  const handleClose = () => {
    setCompareOpen(false);
    clearCompare();
    setStep("pick");
  };

  const show = compareOpen && listings.length >= 2;
  const groups = Array.from(selectedGroups);

  return (
    <AnimatePresence>
      {show && (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-[60] bg-[#FAF7F2]/95 backdrop-blur-sm flex flex-col"
        onClick={(e) => { if (e.target === e.currentTarget) handleClose(); }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[#E5E0D8]">
          <h2 className="text-sm font-semibold text-gray-900">
            Compare {listings.length} Listings
          </h2>
          <button
            onClick={handleClose}
            className="p-1.5 rounded-lg text-gray-500 hover:text-gray-900 hover:bg-[#F3F0EB]"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {step === "pick" ? (
          /* Step 1: Metric picker */
          <div className="flex-1 flex flex-col items-center justify-center px-4">
            <div className="max-w-md w-full space-y-6">
              <div className="text-center">
                <h3 className="text-lg font-semibold text-gray-900">
                  Choose Categories to Compare
                </h3>
                <p className="text-sm text-gray-400 mt-1">
                  Select 2–5 score groups ({selectedGroups.size} selected)
                </p>
              </div>

              <div className="grid grid-cols-2 gap-2">
                {effectiveGroups.map((group) => (
                  <button
                    key={group.key}
                    onClick={() => toggleGroup(group.key)}
                    className={clsx(
                      "flex items-center gap-2 px-3 py-2.5 rounded-xl text-sm font-medium transition-all",
                      selectedGroups.has(group.key)
                        ? "bg-white text-gray-900"
                        : "bg-[#F3F0EB] text-gray-500 hover:bg-gray-200 hover:text-gray-700",
                    )}
                  >
                    <span>{group.icon}</span>
                    <span>{group.label}</span>
                  </button>
                ))}
              </div>

              <button
                onClick={() => setStep("compare")}
                disabled={selectedGroups.size < 2}
                className={clsx(
                  "w-full py-3 rounded-xl font-medium text-sm transition-all",
                  selectedGroups.size >= 2
                    ? "bg-white text-gray-900 hover:bg-gray-100"
                    : "bg-[#F3F0EB] text-gray-400 cursor-not-allowed",
                )}
              >
                Compare →
              </button>
            </div>
          </div>
        ) : (
          /* Step 2: Comparison view */
          <div className="flex-1 flex flex-col overflow-hidden">
            {/* Tabs */}
            <div className="flex items-center gap-1 px-4 py-2 border-b border-[#E5E0D8]">
              {TABS.map(({ key, label }) => (
                <button
                  key={key}
                  onClick={() => setActiveTab(key)}
                  className={clsx(
                    "px-4 py-1.5 rounded-full text-xs font-medium transition-all",
                    activeTab === key
                      ? "bg-white text-gray-900"
                      : "text-gray-500 hover:text-gray-700 hover:bg-[#F3F0EB]",
                  )}
                >
                  {label}
                </button>
              ))}
              <div className="flex-1" />
              <button
                onClick={() => setStep("pick")}
                className="text-xs text-gray-400 hover:text-gray-600"
              >
                Edit metrics
              </button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto px-4 py-4">
              {activeTab === "table" && (
                <CompareTable listings={listings} groups={groups} />
              )}
              {activeTab === "flags" && (
                <CompareFlags listings={listings} groups={groups} />
              )}
              {activeTab === "radar" && (
                <CompareRadar listings={listings} groups={groups} />
              )}
            </div>
          </div>
        )}
      </motion.div>
      )}
    </AnimatePresence>
  );
}
