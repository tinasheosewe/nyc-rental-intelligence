/**
 * PhotoCarousel — Swipeable photo viewer with dot indicators.
 *
 * Used inside FeedCard (detail hero) and ScanCard (thumbnail).
 * Chevron arrows overlaid on the image; touch-swipe supported.
 * Stops propagation so it won't trigger listing-level navigation.
 */

"use client";

import { useState, useCallback, useRef } from "react";
import clsx from "clsx";

interface PhotoCarouselProps {
  photos: string[];
  alt: string;
  /** aspect-ratio class, e.g. "aspect-[16/9]" or "aspect-[4/3]" */
  aspect?: string;
  /** Show chevron arrows on hover (default true) */
  showArrows?: boolean;
  /** Maximum number of dots before switching to "3/7" counter */
  maxDots?: number;
}

export default function PhotoCarousel({
  photos,
  alt,
  aspect = "aspect-[16/9]",
  showArrows = true,
  maxDots = 7,
}: PhotoCarouselProps) {
  const [index, setIndex] = useState(0);
  const touchStart = useRef<number | null>(null);
  const total = photos.length;

  const go = useCallback(
    (delta: number, e?: React.MouseEvent) => {
      e?.stopPropagation();
      e?.preventDefault();
      setIndex((prev) => Math.max(0, Math.min(total - 1, prev + delta)));
    },
    [total],
  );

  const handleTouchStart = (e: React.TouchEvent) => {
    touchStart.current = e.touches[0].clientX;
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    if (touchStart.current === null) return;
    const delta = touchStart.current - e.changedTouches[0].clientX;
    if (Math.abs(delta) > 40) {
      // Only stop propagation when we actually change photos,
      // so listing-level swipe still works on single-photo listings
      if (total > 1) e.stopPropagation();
      setIndex((prev) => Math.max(0, Math.min(total - 1, prev + (delta > 0 ? 1 : -1))));
    }
    touchStart.current = null;
  };

  if (total === 0) return null;

  return (
    <div
      className={clsx("relative w-full bg-zinc-800 group overflow-hidden", aspect)}
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
    >
      {/* Current photo */}
      <img
        src={photos[index]}
        alt={`${alt} — photo ${index + 1}`}
        className="w-full h-full object-cover"
        loading={index === 0 ? "eager" : "lazy"}
        draggable={false}
      />

      {/* Chevron arrows — visible on hover (desktop) */}
      {showArrows && total > 1 && (
        <>
          {index > 0 && (
            <button
              onClick={(e) => go(-1, e)}
              className="absolute left-1.5 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full bg-black/60 backdrop-blur text-white flex items-center justify-center text-sm"
              aria-label="Previous photo"
            >
              ‹
            </button>
          )}
          {index < total - 1 && (
            <button
              onClick={(e) => go(1, e)}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full bg-black/60 backdrop-blur text-white flex items-center justify-center text-sm"
              aria-label="Next photo"
            >
              ›
            </button>
          )}
        </>
      )}

      {/* Indicator: dots for ≤maxDots photos, counter for more */}
      {total > 1 && (
        <div className="absolute bottom-2 left-1/2 -translate-x-1/2 flex items-center">
          {total <= maxDots ? (
            <div className="flex gap-1">
              {photos.map((_, i) => (
                <span
                  key={i}
                  className={clsx(
                    "w-1.5 h-1.5 rounded-full transition-colors",
                    i === index ? "bg-white" : "bg-white/40",
                  )}
                />
              ))}
            </div>
          ) : (
            <span className="text-[10px] font-medium text-white bg-black/50 backdrop-blur px-1.5 py-0.5 rounded-full">
              {index + 1}/{total}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
