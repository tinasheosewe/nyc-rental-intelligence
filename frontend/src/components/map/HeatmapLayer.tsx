/**
 * HeatmapLayer — IDW-interpolated tile-based heatmap for Leaflet.
 *
 * Uses Leaflet's L.GridLayer to render canvas tiles properly integrated
 * with pan/zoom. Each 256×256 tile computes IDW interpolation for its
 * geographic area, so tiles are cached and positioned automatically.
 *
 * Color scale: green (≥75) → yellow (≥50) → orange (≥25) → red (<25).
 * Semi-transparent so the base map remains readable underneath.
 */

"use client";

import { useEffect, useRef } from "react";
import { useMap } from "react-leaflet";
import type { Listing, ScoreDimension } from "@/lib/types";
import L from "leaflet";

interface HeatmapLayerProps {
  listings: Listing[];
  dimension: ScoreDimension;
}

// ── Color interpolation helpers ──────────────────────────────────

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
const POWER = 2;
const PIXEL_STEP = 4; // Render every Nth pixel per tile, then upscale
const OPACITY = 0.28;
const TILE_SIZE = 256;

/** Compute the geographic distance in meters between two points */
function haversineMeters(
  lat1: number, lng1: number,
  lat2: number, lng2: number,
): number {
  const R = 6371000;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// Max influence radius in meters — points beyond this don't contribute
const MAX_RADIUS_M = 3000;

interface DataPoint {
  lat: number;
  lng: number;
  score: number;
}

/**
 * Create a custom L.GridLayer that renders IDW heatmap tiles.
 */
function createHeatmapGridLayer(dataPoints: DataPoint[]) {
  const HeatmapGrid = L.GridLayer.extend({
    createTile(coords: L.Coords) {
      const tile = document.createElement("canvas");
      tile.width = TILE_SIZE;
      tile.height = TILE_SIZE;

      const ctx = tile.getContext("2d");
      if (!ctx || dataPoints.length === 0) return tile;

      const map = this._map as L.Map;
      const tileSize = this.getTileSize();

      // Top-left pixel of this tile in global pixel coords
      const tileOriginPx = coords.scaleBy(tileSize);

      // Get the lat/lng bounds of this tile (with some padding for IDW reach)
      const nw = map.unproject(tileOriginPx, coords.z);
      const se = map.unproject(
        L.point(tileOriginPx.x + tileSize.x, tileOriginPx.y + tileSize.y),
        coords.z,
      );

      // Expand bounds for the influence radius to avoid edge seams
      const latPad = Math.abs(nw.lat - se.lat) * 0.5;
      const lngPad = Math.abs(se.lng - nw.lng) * 0.5;
      const expandedNw = { lat: nw.lat + latPad, lng: nw.lng - lngPad };
      const expandedSe = { lat: se.lat - latPad, lng: se.lng + lngPad };

      // Filter to nearby data points only
      const nearby = dataPoints.filter(
        (d) =>
          d.lat <= expandedNw.lat &&
          d.lat >= expandedSe.lat &&
          d.lng >= expandedNw.lng &&
          d.lng <= expandedSe.lng,
      );

      // If no nearby data points at all, leave tile transparent
      if (nearby.length === 0) return tile;

      // Render at reduced resolution
      const sw = Math.ceil(TILE_SIZE / PIXEL_STEP);
      const sh = Math.ceil(TILE_SIZE / PIXEL_STEP);

      const imgData = ctx.createImageData(sw, sh);
      const pixels = imgData.data;

      for (let sy = 0; sy < sh; sy++) {
        const py = sy * PIXEL_STEP + PIXEL_STEP / 2;
        for (let sx = 0; sx < sw; sx++) {
          const px = sx * PIXEL_STEP + PIXEL_STEP / 2;

          // Convert tile pixel to lat/lng
          const globalPx = L.point(tileOriginPx.x + px, tileOriginPx.y + py);
          const latlng = map.unproject(globalPx, coords.z);

          // IDW interpolation using real geographic distance
          let weightSum = 0;
          let valueSum = 0;
          let hasNearby = false;

          for (const pt of nearby) {
            const dist = haversineMeters(latlng.lat, latlng.lng, pt.lat, pt.lng);
            if (dist > MAX_RADIUS_M) continue;
            hasNearby = true;

            if (dist < 1) {
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

      // Draw low-res then upscale
      const offscreen = new OffscreenCanvas(sw, sh);
      const offCtx = offscreen.getContext("2d");
      if (offCtx) {
        offCtx.putImageData(imgData, 0, 0);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(offscreen, 0, 0, TILE_SIZE, TILE_SIZE);
      }

      return tile;
    },
  });

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return new (HeatmapGrid as any)({
    tileSize: TILE_SIZE,
    opacity: 1,
    updateWhenZooming: false,
    keepBuffer: 2,
  }) as L.GridLayer;
}

export default function HeatmapLayer({ listings, dimension }: HeatmapLayerProps) {
  const map = useMap();
  const layerRef = useRef<L.GridLayer | null>(null);

  useEffect(() => {
    // Build data points
    const dataPoints: DataPoint[] = listings
      .map((l) => ({
        lat: l.latitude,
        lng: l.longitude,
        score: l.scores[dimension] ?? 50,
      }))
      .filter((d) => d.score !== null);

    // Remove old layer
    if (layerRef.current) {
      map.removeLayer(layerRef.current);
      layerRef.current = null;
    }

    if (dataPoints.length === 0) return;

    // Create and add new layer
    const layer = createHeatmapGridLayer(dataPoints);
    layer.addTo(map);
    layerRef.current = layer;

    return () => {
      if (layerRef.current) {
        map.removeLayer(layerRef.current);
        layerRef.current = null;
      }
    };
  }, [map, listings, dimension]);

  return null;
}
