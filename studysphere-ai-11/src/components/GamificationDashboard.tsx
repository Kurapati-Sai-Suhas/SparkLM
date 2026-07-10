import { useEffect, useState } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  Flame,
  Trophy,
  Award,
  Bug,
  Network,
  Zap,
  TrendingUp,
  Crown,
  Medal,
} from "lucide-react";

interface LeaderUser {
  rank: 1 | 2 | 3;
  name: string;
  handle: string;
  elo: number;
}

interface BadgeItem {
  id: string;
  name: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
  color: "primary" | "amber" | "indigo";
}

const LEADERBOARD: LeaderUser[] = [
  { rank: 1, name: "Aarav Mehta",  handle: "@aarav",  elo: 2487 },
  { rank: 2, name: "Priya Sharma", handle: "@priya",  elo: 2412 },
  { rank: 3, name: "Daniel Park",  handle: "@dpark",  elo: 2356 },
];

const BADGES: BadgeItem[] = [
  { id: "b1", name: "100 Bugs Squashed", description: "Resolved 100 errors",   icon: Bug,     color: "primary" },
  { id: "b2", name: "Graph Master",      description: "Solved every DAG node",  icon: Network, color: "indigo"  },
  { id: "b3", name: "Lightning Coder",   description: "10 problems in 1 hour",  icon: Zap,     color: "amber"   },
];

const RANK_META: Record<
  1 | 2 | 3,
  { ring: string; text: string; bg: string; glow: string; label: string }
> = {
  1: {
    ring: "ring-amber-400/50",
    text: "text-amber-300",
    bg: "bg-amber-400/10",
    glow: "shadow-[0_0_15px_rgba(251,191,36,0.35)]",
    label: "Gold",
  },
  2: {
    ring: "ring-slate-300/40",
    text: "text-slate-200",
    bg: "bg-slate-300/10",
    glow: "shadow-[0_0_15px_rgba(203,213,225,0.25)]",
    label: "Silver",
  },
  3: {
    ring: "ring-orange-500/40",
    text: "text-orange-300",
    bg: "bg-orange-500/10",
    glow: "shadow-[0_0_15px_rgba(249,115,22,0.3)]",
    label: "Bronze",
  },
};

const BADGE_COLOR: Record<
  BadgeItem["color"],
  { text: string; bg: string; ring: string; glow: string }
> = {
  primary: {
    text: "text-primary",
    bg: "bg-primary/10",
    ring: "border-primary/30",
    glow: "hover:shadow-[0_0_15px_rgba(59,130,246,0.35)]",
  },
  amber: {
    text: "text-amber-300",
    bg: "bg-amber-400/10",
    ring: "border-amber-400/30",
    glow: "hover:shadow-[0_0_15px_rgba(251,191,36,0.35)]",
  },
  indigo: {
    text: "text-indigo-300",
    bg: "bg-indigo-400/10",
    ring: "border-indigo-400/30",
    glow: "hover:shadow-[0_0_15px_rgba(129,140,248,0.35)]",
  },
};

const cardBase =
  "relative overflow-hidden bg-card/40 backdrop-blur-md border-border/60";

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  Bug, Network, Zap, Award, Flame, Trophy
};

export default function GamificationDashboard() {
  const [streak, setStreak] = useState(0);
  const [leaderboard, setLeaderboard] = useState<LeaderUser[]>(LEADERBOARD);
  const [badges, setBadges] = useState<any[]>(BADGES);

  useEffect(() => {
    fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/coding-portals/gamification/`, {
      headers: { Authorization: `Bearer ${localStorage.getItem('authToken') || localStorage.getItem('access')}` }
    })
    .then(res => res.json())
    .then(data => {
      if (data.streak !== undefined) setStreak(data.streak);
      if (data.leaderboard && data.leaderboard.length > 0) setLeaderboard(data.leaderboard);
      if (data.badges && data.badges.length > 0) setBadges(data.badges);
    })
    .catch(console.error);
  }, []);

  return (
    <div className="w-full max-w-sm space-y-4">
      {/* ============ STREAK ============ */}
      <Card
        data-testid="streak-card"
        className={`${cardBase} hover:-translate-y-0.5 hover:shadow-[0_0_25px_rgba(249,115,22,0.25)] transition-all duration-300`}
      >
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-orange-400/60 to-transparent" />
        <div className="pointer-events-none absolute -top-16 -right-16 h-40 w-40 rounded-full bg-orange-500/20 blur-3xl" />

        <CardContent className="relative p-5 flex items-center gap-4">
          <div className="relative shrink-0">
            <div className="absolute inset-0 rounded-xl bg-orange-500/30 blur-xl" />
            <div className="relative h-14 w-14 rounded-xl border border-orange-400/30 bg-orange-500/10 flex items-center justify-center shadow-[0_0_20px_rgba(249,115,22,0.4)]">
              <Flame className="h-7 w-7 text-orange-300 drop-shadow-[0_0_8px_rgba(249,115,22,0.7)]" />
            </div>
          </div>

          <div className="flex-1 min-w-0">
            <p className="text-[10px] font-medium uppercase tracking-[0.22em] text-muted-foreground">
              Current Streak
            </p>
            <div className="flex items-baseline gap-1.5 mt-0.5">
              <span className="text-3xl font-semibold tabular-nums text-foreground">
                {streak}
              </span>
              <span className="text-sm text-muted-foreground">
                day{streak === 1 ? "" : "s"}
              </span>
            </div>
            <p className="text-[11px] text-orange-300/90 mt-0.5 inline-flex items-center gap-1">
              <TrendingUp className="h-3 w-3" />
              Personal best!
            </p>
          </div>
        </CardContent>
      </Card>

      {/* ============ LEADERBOARD ============ */}
      <Card data-testid="leaderboard-card" className={cardBase}>
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-amber-400/60 to-transparent" />
        <CardHeader className="border-b border-border/60 pb-3">
          <CardTitle className="text-sm font-medium flex items-center justify-between">
            <span className="flex items-center gap-2 text-foreground">
              <Trophy className="h-4 w-4 text-amber-300" />
              Global Leaderboard
            </span>
            <Badge
              variant="outline"
              className="text-[10px] font-mono border-border/60 text-muted-foreground"
            >
              Elo
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-3 space-y-2">
          {LEADERBOARD.map((u) => {
            const m = RANK_META[u.rank];
            return (
              <div
                key={u.rank}
                data-testid={`leader-rank-${u.rank}`}
                className={`group flex items-center gap-3 p-2.5 rounded-lg border border-border/60 bg-background/30
                  hover:-translate-y-0.5 hover:${m.glow}
                  ${m.glow} transition-all duration-300`}
              >
                {/* Rank badge */}
                <div
                  className={`relative h-9 w-9 rounded-lg flex items-center justify-center font-mono text-sm font-semibold ${m.bg} ${m.text} ring-1 ${m.ring}`}
                >
                  {u.rank === 1 ? (
                    <Crown className="h-4 w-4" />
                  ) : (
                    <span>{u.rank}</span>
                  )}
                </div>

                <Avatar className={`h-8 w-8 ring-1 ${m.ring}`}>
                  <AvatarFallback className={`${m.bg} ${m.text} text-xs font-semibold`}>
                    {u.name.charAt(0)}
                  </AvatarFallback>
                </Avatar>

                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground truncate leading-tight">
                    {u.name}
                  </p>
                  <p className="text-[11px] text-muted-foreground truncate">
                    {u.handle}
                  </p>
                </div>

                <div className="text-right">
                  <p className={`text-sm font-mono tabular-nums ${m.text}`}>
                    {u.elo}
                  </p>
                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    {m.label}
                  </p>
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>

      {/* ============ BADGES ============ */}
      <Card data-testid="badges-card" className={cardBase}>
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/60 to-transparent" />
        <CardHeader className="border-b border-border/60 pb-3">
          <CardTitle className="text-sm font-medium flex items-center justify-between text-foreground">
            <span className="flex items-center gap-2">
              <Award className="h-4 w-4 text-primary" />
              Recent Badges
            </span>
            <Badge
              variant="outline"
              className="text-[10px] font-mono border-border/60 text-muted-foreground"
            >
              {BADGES.length} new
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-3 space-y-2">
          {badges.map((b) => {
            const c = BADGE_COLOR[b.color as keyof typeof BADGE_COLOR] || BADGE_COLOR.primary;
            const Icon = ICONS[b.icon] || Award;
            return (
              <div
                key={b.id}
                data-testid={`badge-${b.id}`}
                className={`group flex items-center gap-3 p-2.5 rounded-lg border ${c.ring} bg-background/30
                  hover:-translate-y-0.5 ${c.glow} transition-all duration-300`}
              >
                <div
                  className={`h-9 w-9 rounded-lg ${c.bg} ${c.text} ring-1 ${c.ring} flex items-center justify-center shrink-0
                    group-hover:scale-105 transition-transform`}
                >
                  <Icon className="h-4 w-4" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground truncate leading-tight">
                    {b.name}
                  </p>
                  <p className="text-[11px] text-muted-foreground truncate">
                    {b.description}
                  </p>
                </div>
                <Medal className={`h-3.5 w-3.5 ${c.text} opacity-60 group-hover:opacity-100 transition-opacity`} />
              </div>
            );
          })}
        </CardContent>
      </Card>
    </div>
  );
}
