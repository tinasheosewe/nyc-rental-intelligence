/**
 * ExploreView — Container for Explore tab.
 *
 * Switches between Feed mode (single card) and Scan mode (grid)
 * based on the viewMode state.
 */

"use client";

import { useStore } from "@/lib/store";
import FeedView from "./FeedView";
import ScanView from "./ScanView";

export default function ExploreView() {
  const viewMode = useStore((s) => s.viewMode);

  return viewMode === "feed" ? <FeedView /> : <ScanView />;
}
