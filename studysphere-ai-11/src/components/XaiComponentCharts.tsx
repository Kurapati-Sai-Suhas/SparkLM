import React from 'react';
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import { Brain, Flame } from 'lucide-react';

interface RadarDataPoint {
  subject: string;
  A: number;
  fullMark: number;
}

interface XaiRadarChartProps {
  radarData?: RadarDataPoint[];
  dominantFactor?: string;
}

const XaiComponentCharts: React.FC<XaiRadarChartProps> = ({
  radarData,
  dominantFactor,
}) => {
  const data: RadarDataPoint[] = radarData || [
    { subject: 'Time Complexity', A: 0, fullMark: 100 },
    { subject: 'Space Complexity', A: 0, fullMark: 100 },
    { subject: 'Topic Recency', A: 0, fullMark: 100 },
    { subject: 'Syntax/Logic', A: 0, fullMark: 100 },
  ];

  return (
    <div
      data-testid="xai-radar"
      className="relative overflow-hidden rounded-xl border border-border/60 bg-card/40 backdrop-blur-md shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04),0_0_40px_rgba(99,102,241,0.08)] p-5 max-w-md w-full"
    >
      {/* hairline + glow */}
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/70 to-transparent" />
      <div className="pointer-events-none absolute -top-24 -right-24 h-48 w-48 rounded-full bg-indigo-500/20 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-24 -left-24 h-48 w-48 rounded-full bg-primary/15 blur-3xl" />

      {/* Header */}
      <div className="relative mb-5">
        <h3 className="flex items-center gap-2 text-[11px] font-medium tracking-[0.22em] uppercase text-indigo-300 mb-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-indigo-400 shadow-[0_0_8px_rgba(129,140,248,0.9)]" />
          </span>
          <Brain className="h-3.5 w-3.5" />
          Skill Factor Analysis
        </h3>
        <p className="relative text-sm leading-relaxed text-muted-foreground pl-4">
          <span className="absolute left-0 top-0 bottom-0 w-px bg-gradient-to-b from-primary via-indigo-400 to-transparent shadow-[0_0_8px_rgba(99,102,241,0.6)]" />
          Relative weight of each factor in this recommendation.
        </p>
      </div>

      {/* Radar */}
      <div className="relative h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart cx="50%" cy="50%" outerRadius="70%" data={data}>
            <defs>
              <linearGradient id="xaiRadarFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="hsl(217 91% 60%)" stopOpacity={0.55} />
                <stop offset="100%" stopColor="hsl(243 75% 65%)" stopOpacity={0.15} />
              </linearGradient>
            </defs>
            <PolarGrid stroke="hsl(var(--border))" strokeOpacity={0.5} />
            <PolarAngleAxis
              dataKey="subject"
              tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
            />
            <PolarRadiusAxis
              angle={30}
              domain={[0, 100]}
              tick={false}
              axisLine={false}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: 'hsl(var(--card) / 0.95)',
                borderColor: 'hsl(var(--border))',
                borderRadius: 8,
                color: 'hsl(var(--foreground))',
                backdropFilter: 'blur(8px)',
                fontSize: 12,
              }}
              itemStyle={{ color: 'hsl(217 91% 70%)' }}
              cursor={{ stroke: 'hsl(217 91% 60%)', strokeOpacity: 0.3 }}
            />
            <Radar
              name="Impact Score"
              dataKey="A"
              stroke="hsl(217 91% 60%)"
              strokeWidth={2}
              fill="url(#xaiRadarFill)"
              fillOpacity={1}
            />
          </RadarChart>
        </ResponsiveContainer>
      </div>

      {/* Dominant Factor Footer */}
      <div
        data-testid="xai-dominant-factor"
        className="relative mt-4 flex justify-between items-center rounded-lg border border-border/60 bg-background/40 backdrop-blur px-3.5 py-2.5 overflow-hidden"
      >
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/70 to-transparent" />
        <span className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
          <Flame className="h-3 w-3 text-primary" />
          Dominant Factor
        </span>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-2.5 py-0.5 text-xs font-semibold capitalize text-primary shadow-[0_0_12px_rgba(59,130,246,0.35)]">
          {dominantFactor || 'Processing…'}
        </span>
      </div>
    </div>
  );
};

export default XaiComponentCharts;