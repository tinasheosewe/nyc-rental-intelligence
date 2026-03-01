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

const COLORS = ["#22c55e", "#3b82f6", "#eab308", "#ef4444", "#a855f7"];

export default function CompareRadar({ listings, groups }: CompareRadarProps) {
  // Reshape data for Recharts radar
  const data = groups.map((gk) => {
    const point: Record<string, string | number> = {
      dimension: GROUP_LABELS[gk],
    };
    listings.forEach((l) => {
      point[l.id] = Math.round(getGroupScore(l.scores, gk));
    });
    return point;
  });

  if (listings.length > 3) {
    return (
      <div className="space-y-4">
        <p className="text-xs text-yellow-400 bg-yellow-500/10 px-3 py-2 rounded-lg">
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
          <PolarGrid stroke="#333" />
          <PolarAngleAxis
            dataKey="dimension"
            tick={{ fill: "#a1a1aa", fontSize: 11 }}
          />
          <PolarRadiusAxis
            angle={90}
            domain={[0, 100]}
            tick={{ fill: "#52525b", fontSize: 9 }}
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
            wrapperStyle={{ fontSize: 11, color: "#a1a1aa" }}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#18181b",
              border: "1px solid #333",
              borderRadius: "8px",
              fontSize: 12,
            }}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}
