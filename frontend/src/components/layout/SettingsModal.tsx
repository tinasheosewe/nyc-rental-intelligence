/**
 * SettingsModal — Preferences panel.
 *
 * Contains the drag-to-reorder priority ranking for score dimensions.
 * Top 3 get heaviest weight in composite calculation.
 */

"use client";

import { useState, useCallback } from "react";
import { useStore } from "@/lib/store";
import type { ScoreDimension } from "@/lib/types";
import { DIMENSION_LABELS } from "@/lib/types";
import { motion, AnimatePresence } from "framer-motion";
import clsx from "clsx";

function weightIndicator(index: number): string {
  if (index < 3) return "●●●";
  if (index < 7) return "●●";
  return "●";
}

function weightColor(index: number): string {
  if (index < 3) return "text-green-400";
  if (index < 7) return "text-yellow-400";
  return "text-zinc-600";
}

export default function SettingsModal() {
  const settingsOpen = useStore((s) => s.settingsOpen);
  const setSettingsOpen = useStore((s) => s.setSettingsOpen);
  const priorities = useStore((s) => s.priorities);
  const setPriorities = useStore((s) => s.setPriorities);

  const [draft, setDraft] = useState<ScoreDimension[]>(priorities);

  // Simple drag reorder via buttons (up/down)
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
            className="fixed right-0 top-0 bottom-0 z-50 w-full max-w-md bg-zinc-900 border-l border-zinc-800 overflow-y-auto"
          >
            <div className="px-5 py-4 space-y-6">
              {/* Header */}
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-white">Preferences</h2>
                <button
                  onClick={() => setSettingsOpen(false)}
                  className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              {/* Priority ranking */}
              <div className="space-y-3">
                <div>
                  <h3 className="text-sm font-medium text-zinc-300">
                    Priority Ranking
                  </h3>
                  <p className="text-xs text-zinc-500 mt-0.5">
                    Drag to reorder. Top 3 get 3× weight in composite score.
                  </p>
                </div>

                <div className="space-y-1">
                  {draft.map((dim, idx) => (
                    <div
                      key={dim}
                      className={clsx(
                        "flex items-center gap-3 px-3 py-2.5 rounded-lg border transition-all",
                        idx < 3
                          ? "border-green-500/30 bg-green-500/5"
                          : "border-zinc-800 bg-zinc-800/50",
                      )}
                    >
                      {/* Rank number */}
                      <span className="text-xs text-zinc-600 w-4 text-right font-mono">
                        {idx + 1}
                      </span>

                      {/* Label */}
                      <span className="flex-1 text-sm text-zinc-300">
                        {DIMENSION_LABELS[dim]}
                      </span>

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
                              ? "text-zinc-700 cursor-not-allowed"
                              : "text-zinc-400 hover:text-white",
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
                              ? "text-zinc-700 cursor-not-allowed"
                              : "text-zinc-400 hover:text-white",
                          )}
                        >
                          ▼
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Save */}
              <button
                onClick={handleSave}
                className="w-full py-2.5 rounded-xl text-sm font-medium text-zinc-900 bg-white hover:bg-zinc-200 transition-colors"
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
