/**
 * FeedView — Explore Feed mode container.
 *
 * Single-card full-viewport layout with horizontal navigation
 * between listings via arrow buttons and keyboard arrows.
 */

"use client";

import { useEffect, useCallback, useRef, useState } from "react";
import { useStore } from "@/lib/store";
import FeedCard from "./FeedCard";

export default function FeedView() {
  const listings = useStore((s) => s.listings);
  const feedIndex = useStore((s) => s.feedIndex);
  const setFeedIndex = useStore((s) => s.setFeedIndex);
  const skipped = useStore((s) => s.skipped);
  const isLoading = useStore((s) => s.isLoading);

  const [direction, setDirection] = useState(0);

  // Filter out skipped listings for display
  const visible = listings.filter((l) => !skipped.has(l.id));
  const currentIndex = Math.min(feedIndex, visible.length - 1);
  const current = visible[currentIndex];

  const goTo = useCallback(
    (delta: number) => {
      const next = currentIndex + delta;
      if (next >= 0 && next < visible.length) {
        setDirection(delta);
        setFeedIndex(listings.indexOf(visible[next]));
      }
    },
    [currentIndex, visible, listings, setFeedIndex],
  );

  // Keyboard navigation
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") goTo(-1);
      if (e.key === "ArrowRight") goTo(1);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [goTo]);

  // Touch swipe
  const touchStart = useRef<number | null>(null);
  const handleTouchStart = (e: React.TouchEvent) => {
    touchStart.current = e.touches[0].clientX;
  };
  const handleTouchEnd = (e: React.TouchEvent) => {
    if (touchStart.current === null) return;
    const delta = touchStart.current - e.changedTouches[0].clientX;
    if (Math.abs(delta) > 60) {
      goTo(delta > 0 ? 1 : -1);
    }
    touchStart.current = null;
  };

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-zinc-700 border-t-white rounded-full animate-spin" />
      </div>
    );
  }

  if (visible.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-zinc-500 gap-3">
        <svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
        </svg>
        <p className="text-sm">No listings match your criteria</p>
      </div>
    );
  }

  return (
    <div
      className="flex-1 relative overflow-hidden"
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
    >
      {/* Navigation arrows */}
      {currentIndex > 0 && (
        <button
          onClick={() => goTo(-1)}
          className="absolute left-2 top-1/2 -translate-y-1/2 z-10 w-10 h-10 rounded-full bg-zinc-900/80 backdrop-blur text-zinc-400 hover:text-white flex items-center justify-center transition-colors"
        >
          ‹
        </button>
      )}
      {currentIndex < visible.length - 1 && (
        <button
          onClick={() => goTo(1)}
          className="absolute right-2 top-1/2 -translate-y-1/2 z-10 w-10 h-10 rounded-full bg-zinc-900/80 backdrop-blur text-zinc-400 hover:text-white flex items-center justify-center transition-colors"
        >
          ›
        </button>
      )}

      {/* Position indicator */}
      <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 text-xs text-zinc-600">
        {currentIndex + 1} / {visible.length}
      </div>

      {/* Current card */}
      {current && <FeedCard listing={current} direction={direction} />}
    </div>
  );
}
