/**
 * useEscapeStack — Global Escape key handler for overlays.
 *
 * Closes the topmost open overlay in priority order:
 *   compare > map > filter > settings
 */

"use client";

import { useEffect } from "react";
import { useStore } from "./store";

export function useEscapeStack() {
  const mapOpen = useStore((s) => s.mapOpen);
  const setMapOpen = useStore((s) => s.setMapOpen);
  const compareOpen = useStore((s) => s.compareOpen);
  const setCompareOpen = useStore((s) => s.setCompareOpen);
  const clearCompare = useStore((s) => s.clearCompare);
  const filterSheetOpen = useStore((s) => s.filterSheetOpen);
  const setFilterSheetOpen = useStore((s) => s.setFilterSheetOpen);
  const settingsOpen = useStore((s) => s.settingsOpen);
  const setSettingsOpen = useStore((s) => s.setSettingsOpen);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;

      // Close topmost overlay (highest z-index first)
      if (compareOpen) {
        setCompareOpen(false);
        clearCompare();
      } else if (mapOpen) {
        setMapOpen(false);
      } else if (filterSheetOpen) {
        setFilterSheetOpen(false);
      } else if (settingsOpen) {
        setSettingsOpen(false);
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [
    mapOpen,
    setMapOpen,
    compareOpen,
    setCompareOpen,
    clearCompare,
    filterSheetOpen,
    setFilterSheetOpen,
    settingsOpen,
    setSettingsOpen,
  ]);
}
