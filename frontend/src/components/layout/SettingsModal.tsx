/**
 * SettingsModal — Preferences panel.
 *
 * Scoring preferences:
 *   - "What matters most?" — boost up to TWO score groups (×2 weight)
 *   - "Ignore signals"     — exclude individual dimensions entirely
 *   - Kids Mode            — include school proximity in scoring
 *
 * The old drag-to-rank priority control is gone: every group matters
 * equally by default; boosts are the only weighting knob.
 */

"use client";

import { useEffect, useState } from "react";
import { useStore } from "@/lib/store";
import type { ScoreDimension, ScoreGroupKey } from "@/lib/types";
import {
  GROUP_BY_KEY,
  SCORE_GROUP_KEYS,
  IGNORABLE_DIMENSIONS,
  DIMENSION_LABELS,
  MAX_BOOSTS,
} from "@/lib/types";
import { motion, AnimatePresence } from "framer-motion";
import clsx from "clsx";

export default function SettingsModal() {
  const settingsOpen = useStore((s) => s.settingsOpen);
  const setSettingsOpen = useStore((s) => s.setSettingsOpen);
  const boosts = useStore((s) => s.boosts);
  const ignoredDims = useStore((s) => s.ignoredDims);
  const setScoringPrefs = useStore((s) => s.setScoringPrefs);
  const kidsMode = useStore((s) => s.kidsMode);
  const setKidsMode = useStore((s) => s.setKidsMode);

  const [draftBoosts, setDraftBoosts] = useState<ScoreGroupKey[]>(boosts);
  const [draftIgnored, setDraftIgnored] = useState<ScoreDimension[]>(ignoredDims);

  // Re-seed drafts from the store each time the panel opens.
  useEffect(() => {
    if (settingsOpen) {
      setDraftBoosts(useStore.getState().boosts);
      setDraftIgnored(useStore.getState().ignoredDims);
    }
  }, [settingsOpen]);

  const toggleBoost = (gk: ScoreGroupKey) => {
    setDraftBoosts((prev) => {
      if (prev.includes(gk)) return prev.filter((g) => g !== gk);
      if (prev.length >= MAX_BOOSTS) return prev;
      return [...prev, gk];
    });
  };

  const toggleIgnore = (dim: ScoreDimension) => {
    setDraftIgnored((prev) =>
      prev.includes(dim) ? prev.filter((d) => d !== dim) : [...prev, dim],
    );
  };

  const handleSave = () => {
    setScoringPrefs(draftBoosts, draftIgnored);
    setSettingsOpen(false);
  };

  return (
    <AnimatePresence>
      {settingsOpen && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
            onClick={() => setSettingsOpen(false)}
          />

          {/* Panel */}
          <motion.div
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 25, stiffness: 300 }}
            className="fixed right-0 top-0 bottom-0 z-50 w-full max-w-md bg-white border-l border-[#E5E0D8] overflow-y-auto"
          >
            <div className="px-5 py-4 space-y-6">
              {/* Header */}
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-gray-900">Preferences</h2>
                <button
                  onClick={() => setSettingsOpen(false)}
                  className="p-1.5 rounded-lg text-gray-500 hover:text-gray-900 hover:bg-[#F3F0EB]"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              {/* What matters most? — boost toggles */}
              <div className="space-y-3">
                <div>
                  <h3 className="text-sm font-medium text-gray-600">
                    What matters most?
                  </h3>
                  <p className="text-xs text-gray-400 mt-0.5">
                    Boost up to two categories — they count double in the
                    overall score. Everything else still counts.
                  </p>
                </div>

                <div className="space-y-1">
                  {SCORE_GROUP_KEYS.map((gk) => {
                    const group = GROUP_BY_KEY[gk];
                    const selected = draftBoosts.includes(gk);
                    const atLimit = !selected && draftBoosts.length >= MAX_BOOSTS;
                    return (
                      <button
                        key={gk}
                        onClick={() => toggleBoost(gk)}
                        disabled={atLimit}
                        className={clsx(
                          "w-full flex items-center gap-3 px-3 py-3 rounded-lg border text-left transition-all",
                          selected
                            ? "border-green-500/40 bg-green-500/10"
                            : atLimit
                              ? "border-[#E5E0D8] bg-[#F3F0EB] opacity-50 cursor-not-allowed"
                              : "border-[#E5E0D8] bg-[#F3F0EB] hover:border-gray-300",
                        )}
                      >
                        <span className="text-base">{group.icon}</span>
                        <div className="flex-1 min-w-0">
                          <span className="text-sm text-gray-600 font-medium">
                            {group.label}
                          </span>
                          <p className="text-xs text-gray-400 truncate">
                            {group.description}
                          </p>
                        </div>
                        <span
                          className={clsx(
                            "text-xs font-mono shrink-0",
                            selected ? "text-green-500" : "text-gray-300",
                          )}
                        >
                          {selected ? "×2" : "×1"}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Ignore signals — per-dimension exclusion */}
              <div className="space-y-3">
                <div>
                  <h3 className="text-sm font-medium text-gray-600">
                    Ignore signals
                  </h3>
                  <p className="text-xs text-gray-400 mt-0.5">
                    Signals you don&apos;t care about are excluded from
                    scoring entirely.
                  </p>
                </div>

                <div className="space-y-1">
                  {IGNORABLE_DIMENSIONS.map((dim) => {
                    const checked = draftIgnored.includes(dim);
                    return (
                      <label
                        key={dim}
                        className="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-[#E5E0D8] bg-[#F3F0EB] cursor-pointer hover:border-gray-300 transition-all"
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleIgnore(dim)}
                          className="w-4 h-4 rounded border-gray-300 accent-amber-500"
                        />
                        <span
                          className={clsx(
                            "text-sm font-medium",
                            checked ? "text-gray-400 line-through" : "text-gray-600",
                          )}
                        >
                          {DIMENSION_LABELS[dim]}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>

              {/* Kids mode toggle */}
              <div className="space-y-3">
                <div>
                  <h3 className="text-sm font-medium text-gray-600">
                    Kids Mode
                  </h3>
                  <p className="text-xs text-gray-400 mt-0.5">
                    Include school proximity scores in rankings.
                  </p>
                </div>

                <button
                  onClick={() => setKidsMode(!kidsMode)}
                  className={clsx(
                    "relative inline-flex h-7 w-12 items-center rounded-full transition-colors",
                    kidsMode ? "bg-green-500" : "bg-gray-200",
                  )}
                >
                  <span
                    className={clsx(
                      "inline-block h-5 w-5 transform rounded-full bg-white transition-transform",
                      kidsMode ? "translate-x-6" : "translate-x-1",
                    )}
                  />
                </button>
              </div>

              {/* Save */}
              <button
                onClick={handleSave}
                className="w-full py-2.5 rounded-xl text-sm font-medium text-gray-900 bg-white hover:bg-gray-100 transition-colors"
              >
                Save Preferences
              </button>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
