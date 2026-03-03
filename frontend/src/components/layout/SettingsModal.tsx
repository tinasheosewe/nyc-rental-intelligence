/**
 * SettingsModal — Preferences panel.
 *
 * Contains the drag-to-reorder priority ranking for score groups.
 * Top 2 get heaviest weight in composite calculation.
 */

"use client";

import { useState, useCallback } from "react";
import { useStore } from "@/lib/store";
import type { ScoreGroupKey } from "@/lib/types";
import { GROUP_BY_KEY } from "@/lib/types";
import { motion, AnimatePresence } from "framer-motion";
import clsx from "clsx";

function weightIndicator(index: number): string {
  if (index < 2) return "●●●";
  if (index < 3) return "●●";
  return "●";
}

function weightColor(index: number): string {
  if (index < 2) return "text-green-400";
  if (index < 3) return "text-yellow-400";
  return "text-gray-400";
}

export default function SettingsModal() {
  const settingsOpen = useStore((s) => s.settingsOpen);
  const setSettingsOpen = useStore((s) => s.setSettingsOpen);
  const priorities = useStore((s) => s.priorities);
  const setPriorities = useStore((s) => s.setPriorities);
  const kidsMode = useStore((s) => s.kidsMode);
  const setKidsMode = useStore((s) => s.setKidsMode);

  const [draft, setDraft] = useState<ScoreGroupKey[]>(priorities);

  const moveUp = useCallback(
    (idx: number) => {
      if (idx === 0) return;
      const next = [...draft];
      [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
      setDraft(next);
    },
    [draft],
  );

  const moveDown = useCallback(
    (idx: number) => {
      if (idx >= draft.length - 1) return;
      const next = [...draft];
      [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
      setDraft(next);
    },
    [draft],
  );

  const handleSave = () => {
    setPriorities(draft);
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

              {/* Priority ranking */}
              <div className="space-y-3">
                <div>
                  <h3 className="text-sm font-medium text-gray-600">
                    Priority Ranking
                  </h3>
                  <p className="text-xs text-gray-400 mt-0.5">
                    Reorder categories. Top 2 get 3× weight in composite score.
                  </p>
                </div>

                <div className="space-y-1">
                  {draft.map((gk, idx) => {
                    const group = GROUP_BY_KEY[gk];
                    return (
                      <div
                        key={gk}
                        className={clsx(
                          "flex items-center gap-3 px-3 py-3 rounded-lg border transition-all",
                          idx < 2
                            ? "border-green-500/30 bg-green-500/5"
                            : idx < 3
                              ? "border-yellow-500/20 bg-yellow-500/5"
                              : "border-[#E5E0D8] bg-[#F3F0EB]",
                        )}
                      >
                        {/* Rank number */}
                        <span className="text-xs text-gray-400 w-4 text-right font-mono">
                          {idx + 1}
                        </span>

                        {/* Icon */}
                        <span className="text-base">{group.icon}</span>

                        {/* Label + description */}
                        <div className="flex-1 min-w-0">
                          <span className="text-sm text-gray-600 font-medium">
                            {group.label}
                          </span>
                          <p className="text-xs text-gray-400 truncate">
                            {group.description}
                          </p>
                        </div>

                        {/* Weight indicator */}
                        <span className={clsx("text-xs font-mono", weightColor(idx))}>
                          {weightIndicator(idx)}
                        </span>

                        {/* Move buttons */}
                        <div className="flex flex-col gap-0.5">
                          <button
                            onClick={() => moveUp(idx)}
                            disabled={idx === 0}
                            className={clsx(
                              "text-xs leading-none px-1",
                              idx === 0
                                ? "text-gray-300 cursor-not-allowed"
                                : "text-gray-500 hover:text-gray-900",
                            )}
                          >
                            ▲
                          </button>
                          <button
                            onClick={() => moveDown(idx)}
                            disabled={idx === draft.length - 1}
                            className={clsx(
                              "text-xs leading-none px-1",
                              idx === draft.length - 1
                                ? "text-gray-300 cursor-not-allowed"
                                : "text-gray-500 hover:text-gray-900",
                            )}
                          >
                            ▼
                          </button>
                        </div>
                      </div>
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
