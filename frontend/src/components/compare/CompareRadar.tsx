/**
 * CompareRadar — Radar/spider chart tab of Compare mode.
 *
 * Overlays 2-3 listings as colored polygons on a radar chart
 * using Recharts. Axes = score groups (not individual dimensions).
 */

"use client";

import type { Listing, ScoreGroupKey } from "@/lib/types";
import { GROUP_LABELS } from "@/lib/types";
import { getGroupScore } from "@/lib/utils";
import { useStore } from "@/lib/store";
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
  Legend,
  Tooltip,
} from "recharts";

interface CompareRadarProps {
  listings: Listing[];
  groups: ScoreGroupKey[];
}

const COLORS = ["#0EA5E9", "#8B5CF6", "#F97316", "#EF4444", "#F59E0B"];

export default function CompareRadar({ listings, groups }: CompareRadarProps) {
  const kidsMode = useStore((s) => s.kidsMode);
  // Reshape data for Recharts radar
  const data = groups.map((gk) => {
    const point: Record<string, string | number> = {
      dimension: GROUP_LABELS[gk],
    };
    listings.forEach((l) => {
      const gs = getGroupScore(l.scores, gk, kidsMode);
      point[l.id] = gs !== null ? Math.round(gs) : 0;
    });
    return point;
  });

  if (listings.length > 3) {
    return (
      <div className="space-y-4">
        <p className="text-xs text-amber-600 bg-amber-50 px-3 py-2 rounded-lg">
          Radar chart works best with 2-3 listings. Consider using Table or Flags
          view for {listings.length} listings.
        </p>
        <RadarChartInner data={data} listings={listings} />
      </div>
    );
  }

  return <RadarChartInner data={data} listings={listings} />;
}

function RadarChartInner({
  data,
  listings,
}: {
  data: Record<string, string | number>[];
  listings: Listing[];
}) {
  return (
    <div className="w-full h-[400px]">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={data} cx="50%" cy="50%" outerRadius="75%">
          <PolarGrid stroke="#E5E0D8" />
          <PolarAngleAxis
            dataKey="dimension"
            tick={{ fill: "#6B7280", fontSize: 11 }}
          />
          <PolarRadiusAxis
            angle={90}
            domain={[0, 100]}
            tick={{ fill: "#9CA3AF", fontSize: 9 }}
            axisLine={false}
          />
          {listings.map((l, i) => (
            <Radar
              key={l.id}
              name={`${l.address.slice(0, 20)}…`}
              dataKey={l.id}
              stroke={COLORS[i % COLORS.length]}
              fill={COLORS[i % COLORS.length]}
              fillOpacity={0.15}
              strokeWidth={2}
            />
          ))}
          <Legend
            wrapperStyle={{ fontSize: 11, color: "#6B7280" }}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#FFFFFF",
              border: "1px solid #E5E0D8",
              borderRadius: "8px",
              fontSize: 12,
            }}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}
