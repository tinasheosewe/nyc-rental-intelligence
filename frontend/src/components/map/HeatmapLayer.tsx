/**
 * HeatmapLayer — IDW-interpolated canvas overlay for the Leaflet map.
 *
 * Renders a smooth, continuous heatmap of a chosen score dimension across
 * the entire visible map area using Inverse Distance Weighting (IDW).
 * The overlay updates on map move/zoom with debounced rendering.
 *
 * Color scale: green (≥75) → yellow (≥50) → orange (≥25) → red (<25).
 * Semi-transparent so the base map remains readable underneath.
 */

"use client";

import { useEffect, useRef, useCallback } from "react";
import { useMap } from "react-leaflet";
import type { Listing, ScoreDimension } from "@/lib/types";

interface HeatmapLayerProps {
  listings: Listing[];
  dimension: ScoreDimension;
}

// ── Color interpolation helpers ──────────────────────────────────

/** Linearly interpolate between two [r,g,b] colors */
function lerpColor(
  a: [number, number, number],
  b: [number, number, number],
  t: number,
): [number, number, number] {
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ];
}

// Color stops: 0 → red, 25 → orange, 50 → yellow, 75 → green
const RED: [number, number, number] = [239, 68, 68];
const ORANGE: [number, number, number] = [249, 115, 22];
const YELLOW: [number, number, number] = [234, 179, 8];
const GREEN: [number, number, number] = [34, 197, 94];

function scoreToRgb(score: number): [number, number, number] {
  const s = Math.max(0, Math.min(100, score));
  if (s >= 75) return lerpColor(YELLOW, GREEN, (s - 75) / 25);
  if (s >= 50) return lerpColor(ORANGE, YELLOW, (s - 50) / 25);
  if (s >= 25) return lerpColor(RED, ORANGE, (s - 25) / 25);
  return RED;
}

// ── IDW parameters ───────────────────────────────────────────────
const POWER = 2; // IDW exponent — 2 gives smooth falloff
const PIXEL_STEP = 6; // Render every Nth pixel for performance
const OPACITY = 0.30; // Heatmap alpha

export default function HeatmapLayer({ listings, dimension }: HeatmapLayerProps) {
  const map = useMap();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameRef = useRef<number>(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Build data points array (pixel-independent) ──────────────
  const getDataPoints = useCallback(() => {
    return listings
      .map((l) => ({
        lat: l.latitude,
        lng: l.longitude,
        score: l.scores[dimension] ?? 50,
      }))
      .filter((d) => d.score !== null);
  }, [listings, dimension]);

  // ── Render the heatmap canvas ────────────────────────────────
  const render = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const container = map.getContainer();
    const w = container.clientWidth;
    const h = container.clientHeight;

    // Size canvas to map container
    canvas.width = w;
    canvas.height = h;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);

    const dataPoints = getDataPoints();
    if (dataPoints.length === 0) return;

    // Project data points to pixel coordinates
    const projected = dataPoints.map((d) => {
      const pt = map.latLngToContainerPoint([d.lat, d.lng]);
      return { x: pt.x, y: pt.y, score: d.score };
    });

    // Determine max influence radius in pixels (adaptive to zoom)
    // At zoom 12 (~city), use ~200px; at zoom 15 (~block), use ~400px
    const zoom = map.getZoom();
    const maxRadius = Math.min(600, Math.max(120, 50 * Math.pow(2, zoom - 10)));

    // Render low-res then scale
    const sw = Math.ceil(w / PIXEL_STEP);
    const sh = Math.ceil(h / PIXEL_STEP);

    const imgData = ctx.createImageData(sw, sh);
    const pixels = imgData.data;

    for (let sy = 0; sy < sh; sy++) {
      const py = sy * PIXEL_STEP + PIXEL_STEP / 2;
      for (let sx = 0; sx < sw; sx++) {
        const px = sx * PIXEL_STEP + PIXEL_STEP / 2;

        // IDW interpolation
        let weightSum = 0;
        let valueSum = 0;
        let hasNearby = false;

        for (const pt of projected) {
          const dx = px - pt.x;
          const dy = py - pt.y;
          const distSq = dx * dx + dy * dy;
          const dist = Math.sqrt(distSq);

          if (dist > maxRadius) continue;
          hasNearby = true;

          if (dist < 1) {
            // Exactly on a data point
            weightSum = 1;
            valueSum = pt.score;
            break;
          }

          const weight = 1 / Math.pow(dist, POWER);
          weightSum += weight;
          valueSum += weight * pt.score;
        }

        if (!hasNearby || weightSum === 0) continue;

        const score = valueSum / weightSum;
        const [r, g, b] = scoreToRgb(score);

        const idx = (sy * sw + sx) * 4;
        pixels[idx] = r;
        pixels[idx + 1] = g;
        pixels[idx + 2] = b;
        pixels[idx + 3] = Math.round(OPACITY * 255);
      }
    }

    // Draw the low-res image then scale up
    const offscreen = new OffscreenCanvas(sw, sh);
    const offCtx = offscreen.getContext("2d");
    if (!offCtx) return;
    offCtx.putImageData(imgData, 0, 0);

    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(offscreen, 0, 0, w, h);
  }, [map, getDataPoints]);

  // ── Debounced render on map events ───────────────────────────
  const scheduleRender = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    cancelAnimationFrame(frameRef.current);
    timerRef.current = setTimeout(() => {
      frameRef.current = requestAnimationFrame(render);
    }, 80);
  }, [render]);

  // ── Setup canvas + event listeners ───────────────────────────
  useEffect(() => {
    const container = map.getContainer();

    // Create canvas if not already
    let canvas = canvasRef.current;
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.style.position = "absolute";
      canvas.style.top = "0";
      canvas.style.left = "0";
      canvas.style.pointerEvents = "none";
      canvas.style.zIndex = "250"; // Above tiles (200) but below markers (600+)
      canvasRef.current = canvas;
    }

    // Insert into map pane
    const overlayPane = container.querySelector(".leaflet-overlay-pane");
    if (overlayPane && !overlayPane.contains(canvas)) {
      overlayPane.appendChild(canvas);
    }

    // Listen for map movement
    map.on("moveend", scheduleRender);
    map.on("zoomend", scheduleRender);
    map.on("resize", scheduleRender);

    // Initial render
    scheduleRender();

    return () => {
      map.off("moveend", scheduleRender);
      map.off("zoomend", scheduleRender);
      map.off("resize", scheduleRender);
      if (timerRef.current) clearTimeout(timerRef.current);
      cancelAnimationFrame(frameRef.current);
      if (canvas && canvas.parentNode) {
        canvas.parentNode.removeChild(canvas);
      }
      canvasRef.current = null;
    };
  }, [map, scheduleRender]);

  // Re-render when dimension or listings change
  useEffect(() => {
    scheduleRender();
  }, [dimension, listings, scheduleRender]);

  return null; // Canvas managed imperatively
}
