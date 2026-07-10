import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  Users,
  Clock,
  BookOpen,
  Trophy,
  Calendar,
  TrendingUp,
  Activity,
  ArrowRight,
  ArrowUpRight,
  Database,
  RefreshCw,
  Server,
  Sparkles,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import api, { userAPI } from "@/services/api";
import GamificationDashboard from "@/components/GamificationDashboard";

// Dummy shape data for the Study Hours sparkline (the BIG number stays = stats.study_hours)
const STUDY_TREND = [
  { v: 2 }, { v: 4 }, { v: 3 }, { v: 6 },
  { v: 5 }, { v: 8 }, { v: 7 }, { v: 10 },
];

export default function Dashboard() {
  const [stats, setStats] = useState({
    username: "Student",
    active_groups: 0,
    study_hours: 0,
    quizzes_taken: 0,
    achievement_points: 0,
  });

  const [realGroups, setRealGroups] = useState<any[]>([]);
  const [mlops, setMlops] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadDashboard = async () => {
      try {
        const statsRes = await userAPI.getDashboardStats();
        const statsData = statsRes.data || {};
        const profileRes = await userAPI.getProfile();
        const profileData = profileRes.data || {};

        const groupsRes = await api.get("/groups/");
        const fetchedGroups = groupsRes.data.results || groupsRes.data || [];

        const totalGroups = statsData.active_groups || 0;

        if (totalGroups === 0) {
          setRealGroups([]);
        } else {
          setRealGroups(fetchedGroups.slice(0, 4));
        }

        setStats({
          username: profileData.username || "Student",
          active_groups: totalGroups,
          study_hours: statsData.study_hours || 0,
          quizzes_taken: statsData.quizzes_taken || 0,
          achievement_points: statsData.achievement_points || 0,
        });

        // Load MLOps Data
        try {
          const mlopsRes = await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/mlops/telemetry/`, {
            headers: { Authorization: `Bearer ${localStorage.getItem('authToken') || localStorage.getItem('access')}` }
          });
          if (mlopsRes.ok) {
            setMlops(await mlopsRes.json());
          }
        } catch (e) {
          console.error("Failed to load MLOps telemetry");
        }
      } catch (error) {
        console.error("❌ Failed to load dashboard:", error);
      } finally {
        setLoading(false);
      }
    };
    loadDashboard();
  }, []);

  const getMotivationalQuote = (points: number) => {
    if (points < 20) return "You're just getting started. Every big journey begins with a single step!";
    if (points < 50) return "Building momentum! Keep pushing, you are doing great.";
    if (points < 100) return "You're on a great learning streak. Keep up the momentum and crush your goals!";
    if (points < 200) return "You are an absolute machine! Your dedication is really paying off.";
    if (points < 350) return "Unstoppable! You're making incredible progress this week.";
    if (points < 500) return "Outstanding work. Your study habits are elite level right now.";
    if (points < 800) return "Master class! You are dominating your study goals.";
    return "Legendary status! You are in the top tier of learners.";
  };

  // SparkLM premium glass card
  const cardBase =
    "relative overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-2xl " +
    "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)] " +
    "hover:border-indigo-400/25 hover:shadow-[0_8px_40px_-8px_rgba(99,102,241,0.35)] " +
    "hover:-translate-y-0.5 transition-all duration-300";

  return (
    <div
      data-testid="dashboard-page"
      className="relative space-y-8 max-w-7xl mx-auto w-full p-6 md:p-10 pb-14 font-sans"
    >
      {/* HERO */}
      <div
        data-testid="dashboard-hero"
        className="animate-in slide-in-from-bottom-4 fade-in duration-500 relative overflow-hidden rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.04] via-white/[0.02] to-transparent backdrop-blur-2xl p-8 md:p-10"
      >
        {/* faint grid */}
        <div
          className="absolute inset-0 opacity-[0.05] pointer-events-none"
          style={{
            backgroundImage:
              "linear-gradient(to right, rgba(255,255,255,1) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,1) 1px, transparent 1px)",
            backgroundSize: "40px 40px",
            maskImage: "radial-gradient(ellipse at center, black 0%, transparent 75%)",
          }}
        />
        <div className="absolute -top-32 -right-24 h-80 w-80 rounded-full bg-indigo-500/25 blur-[100px]" />
        <div className="absolute -bottom-32 -left-24 h-72 w-72 rounded-full bg-blue-500/15 blur-[100px]" />

        <div className="relative z-10 flex flex-col md:flex-row md:items-center md:justify-between gap-6">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-indigo-400/20 bg-indigo-500/[0.08] backdrop-blur px-3 py-1.5 text-[11px] font-medium text-indigo-200 shadow-[0_0_20px_rgba(99,102,241,0.15)]">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-indigo-400" />
              </span>
              Live session ready
              <Sparkles className="h-3 w-3 text-indigo-300" />
            </div>
            <h1 className="mt-5 text-4xl sm:text-5xl lg:text-6xl font-semibold tracking-tight text-white">
              Welcome back,{" "}
              <span className="bg-gradient-to-r from-indigo-300 via-indigo-400 to-blue-400 bg-clip-text text-transparent">
                {loading ? "…" : stats.username}
              </span>
            </h1>
            <p className="mt-4 text-base md:text-lg text-slate-400 max-w-2xl leading-relaxed">
              {getMotivationalQuote(stats.achievement_points)}
            </p>
          </div>

          <Button
            data-testid="hero-cta-continue"
            asChild
            className="bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 text-white shadow-[0_0_25px_rgba(99,102,241,0.45)] hover:shadow-[0_0_40px_rgba(99,102,241,0.7)] transition-all duration-300 group h-12 px-6 rounded-xl font-medium border border-indigo-400/30"
          >
            <Link to="/groups">
              Continue Learning
              <ArrowRight className="ml-2 h-4 w-4 group-hover:translate-x-1 transition-transform" />
            </Link>
          </Button>
        </div>
      </div>

      {/* STATS */}
      <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-4">
        {/* Active Groups */}
        <Card
          data-testid="stat-active-groups"
          className={`animate-in slide-in-from-bottom-6 fade-in duration-500 delay-100 ${cardBase}`}
        >
          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/50 to-transparent" />
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-[10px] font-semibold uppercase tracking-[0.22em] text-slate-400">
              Active Groups
            </CardTitle>
            <div className="h-9 w-9 rounded-xl border border-indigo-400/20 bg-indigo-500/10 backdrop-blur flex items-center justify-center shadow-[0_0_15px_rgba(99,102,241,0.2)]">
              <Users className="h-4 w-4 text-indigo-300" />
            </div>
          </CardHeader>
          <CardContent className="pt-2">
            <div className="text-5xl font-semibold tracking-tight text-white tabular-nums">
              {loading ? "—" : stats.active_groups}
            </div>
            <p className="mt-2 inline-flex items-center gap-1 text-xs text-emerald-400 font-medium">
              <TrendingUp className="h-3 w-3" /> Keep networking
            </p>
          </CardContent>
        </Card>

        {/* Study Hours — sparkline */}
        <Card
          data-testid="stat-study-hours"
          className={`animate-in slide-in-from-bottom-6 fade-in duration-500 delay-150 ${cardBase}`}
        >
          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-blue-400/50 to-transparent" />
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-[10px] font-semibold uppercase tracking-[0.22em] text-slate-400">
              Study Hours
            </CardTitle>
            <div className="h-9 w-9 rounded-xl border border-blue-400/20 bg-blue-500/10 backdrop-blur flex items-center justify-center shadow-[0_0_15px_rgba(59,130,246,0.2)]">
              <Clock className="h-4 w-4 text-blue-300" />
            </div>
          </CardHeader>
          <CardContent className="pt-2 pb-0">
            <div className="text-5xl font-semibold tracking-tight text-white tabular-nums">
              {loading ? "—" : stats.study_hours}
            </div>
            <p className="mt-2 text-xs text-slate-400">Total time invested</p>

            <div className="-mx-6 -mb-4 h-16 mt-3">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={STUDY_TREND} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="studyArea" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="hsl(243 75% 65%)" stopOpacity={0.55} />
                      <stop offset="100%" stopColor="hsl(243 75% 65%)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <Area
                    type="monotone"
                    dataKey="v"
                    stroke="hsl(243 75% 65%)"
                    strokeWidth={2}
                    fill="url(#studyArea)"
                    isAnimationActive
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        {/* Quizzes Passed */}
        <Card
          data-testid="stat-quizzes-passed"
          className={`animate-in slide-in-from-bottom-6 fade-in duration-500 delay-200 ${cardBase}`}
        >
          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-purple-400/50 to-transparent" />
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-[10px] font-semibold uppercase tracking-[0.22em] text-slate-400">
              Quizzes Passed
            </CardTitle>
            <div className="h-9 w-9 rounded-xl border border-purple-400/20 bg-purple-500/10 backdrop-blur flex items-center justify-center shadow-[0_0_15px_rgba(168,85,247,0.2)]">
              <BookOpen className="h-4 w-4 text-purple-300" />
            </div>
          </CardHeader>
          <CardContent className="pt-2">
            <div className="text-5xl font-semibold tracking-tight text-white tabular-nums">
              {loading ? "—" : stats.quizzes_taken}
            </div>
            <p className="mt-2 text-xs text-slate-400">Knowledge verified</p>
          </CardContent>
        </Card>

        {/* Achievement Points */}
        <Card
          data-testid="stat-achievement-points"
          className={`animate-in slide-in-from-bottom-6 fade-in duration-500 delay-300 ${cardBase}`}
        >
          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-amber-400/60 to-transparent" />
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-[10px] font-semibold uppercase tracking-[0.22em] text-slate-400">
              Points
            </CardTitle>
            <div className="h-9 w-9 rounded-xl border border-amber-400/20 bg-amber-500/10 backdrop-blur flex items-center justify-center shadow-[0_0_15px_rgba(251,191,36,0.2)]">
              <Trophy className="h-4 w-4 text-amber-300" />
            </div>
          </CardHeader>
          <CardContent className="pt-2">
            <div className="text-5xl font-semibold tracking-tight text-white tabular-nums">
              {loading ? "—" : stats.achievement_points}
            </div>
            <p className="mt-2 text-xs font-medium text-amber-300">Rank · Scholar</p>
          </CardContent>
        </Card>
      </div>

      {/* BOTTOM GRID */}
      <div className="grid gap-6 md:grid-cols-12">
        {/* My Study Groups */}
        <Card
          data-testid="study-groups-card"
          className={`md:col-span-7 animate-in slide-in-from-bottom-8 fade-in duration-500 delay-300 ${cardBase}`}
        >
          <CardHeader className="border-b border-white/[0.06] pb-4">
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2 text-white text-base font-medium tracking-tight">
                <Activity className="h-4 w-4 text-indigo-400" />
                My Study Groups
              </CardTitle>
              <Button
                data-testid="explore-groups-btn"
                variant="ghost"
                size="sm"
                className="text-slate-400 hover:text-white hover:bg-white/[0.04] group"
                asChild
              >
                <Link to="/groups">
                  Explore all
                  <ArrowUpRight className="ml-1 h-3.5 w-3.5 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform" />
                </Link>
              </Button>
            </div>
          </CardHeader>
          <CardContent className="pt-6 space-y-5">
            {realGroups.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 text-center">
                <div className="h-14 w-14 rounded-2xl border border-dashed border-white/10 bg-white/[0.02] flex items-center justify-center mb-3">
                  <Users className="h-5 w-5 text-slate-500" />
                </div>
                <p className="text-sm text-slate-400">
                  You haven't joined any groups yet.
                </p>
                <Button
                  data-testid="empty-state-find-group"
                  asChild
                  variant="link"
                  className="text-indigo-300 hover:text-indigo-200 mt-1"
                >
                  <Link to="/groups">Find your first group →</Link>
                </Button>
              </div>
            ) : (
              realGroups.map((group, idx) => {
                const accents = [
                  "bg-indigo-400",
                  "bg-blue-400",
                  "bg-purple-400",
                  "bg-emerald-400",
                ];
                const accent = accents[idx % accents.length];
                const membersCount = group.members ? group.members.length : 0;
                const capacity = group.capacity || 10;
                const pct = Math.min(100, (membersCount / capacity) * 100);

                return (
                  <div
                    key={group.id}
                    data-testid={`group-row-${group.id}`}
                    className="space-y-2 group"
                  >
                    <div className="flex items-center justify-between text-sm">
                      <div className="flex items-center gap-2.5 min-w-0">
                        <span className={`h-2 w-2 rounded-full ${accent} shadow-[0_0_10px_currentColor]`} />
                        <span className="font-medium text-white truncate">
                          {group.name}
                        </span>
                      </div>
                      <span className="text-xs text-slate-400 tabular-nums font-mono">
                        {membersCount} / {capacity}
                      </span>
                    </div>
                    <Progress
                      value={pct}
                      className="h-1.5 bg-white/[0.04] [&>div]:bg-gradient-to-r [&>div]:from-indigo-500 [&>div]:to-indigo-400 [&>div]:shadow-[0_0_12px_rgba(99,102,241,0.6)]"
                    />
                  </div>
                );
              })
            )}
          </CardContent>
        </Card>

        {/* Right column */}
        <div className="md:col-span-5 space-y-6">
          <GamificationDashboard />

          {/* Recent Activity */}
          <Card
            data-testid="recent-activity-card"
            className={`animate-in slide-in-from-bottom-8 fade-in duration-500 delay-500 ${cardBase}`}
          >
            <CardHeader className="border-b border-white/[0.06] pb-4">
              <CardTitle className="text-white text-base font-medium tracking-tight">
                Recent Activity
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-4">
              <ul className="relative space-y-5">
                <li className="absolute left-[5px] top-2 bottom-2 w-px bg-gradient-to-b from-indigo-400/50 via-white/[0.06] to-transparent" />

                <li className="relative flex items-start gap-4 pl-1">
                  <span className="relative mt-1.5 h-2.5 w-2.5 rounded-full bg-indigo-400 shadow-[0_0_12px_rgba(99,102,241,0.9)] shrink-0" />
                  <div className="min-w-0">
                    <p className="text-sm text-white">
                      Logged in to{" "}
                      <span className="font-medium text-indigo-300">Virtual Study Space</span>
                    </p>
                    <p className="text-xs text-slate-500 mt-0.5">Just now</p>
                  </div>
                </li>
              </ul>
            </CardContent>
          </Card>

          {/* Upcoming Sessions */}
          <Card
            data-testid="upcoming-sessions-card"
            className={`relative overflow-hidden animate-in slide-in-from-bottom-8 fade-in duration-500 delay-700 ${cardBase}`}
          >
            <div className="absolute -top-24 -right-16 h-56 w-56 rounded-full bg-indigo-500/30 blur-3xl" />
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/70 to-transparent" />

            <CardContent className="relative p-6">
              <div className="flex items-start gap-4">
                <div className="h-11 w-11 rounded-xl border border-indigo-400/25 bg-indigo-500/10 backdrop-blur flex items-center justify-center shadow-[0_0_18px_rgba(99,102,241,0.35)]">
                  <Calendar className="h-5 w-5 text-indigo-300" />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-base font-medium text-white tracking-tight">
                    Upcoming Sessions
                  </h3>
                  <p className="text-sm text-slate-400 mt-0.5">
                    No study sessions scheduled for today.
                  </p>
                </div>
              </div>

              <Button
                data-testid="schedule-now-btn"
                asChild
                className="mt-5 w-full h-11 rounded-xl bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 text-white shadow-[0_0_18px_rgba(99,102,241,0.5)] hover:shadow-[0_0_30px_rgba(99,102,241,0.75)] transition-all duration-300 group font-medium border border-indigo-400/30"
              >
                <Link to="/schedule">
                  Schedule Now
                  <ArrowRight className="ml-2 h-4 w-4 group-hover:translate-x-1 transition-transform" />
                </Link>
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* MLOps TELEMETRY DASHBOARD */}
      {mlops && (
        <div className="mt-14 animate-in slide-in-from-bottom-8 fade-in duration-500 delay-700 space-y-6">
          <div className="flex items-center justify-between flex-wrap gap-4">
            <div>
              <div className="inline-flex items-center gap-2 rounded-full border border-indigo-400/20 bg-indigo-500/[0.08] backdrop-blur px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-indigo-200 mb-3">
                <Database className="h-3 w-3" /> MLOps · Phase 1 + Phase 2
              </div>
              <h2 className="text-2xl font-semibold text-white tracking-tight">
                Telemetry Dashboard
              </h2>
              <p className="text-sm text-slate-400 mt-1">
                Visualizing Data Flywheel & Autonomous Retraining
              </p>
            </div>
            <Button
              onClick={() => alert("🚀 Triggering Phase 2: Autonomous Retraining Pipeline in the backend...")}
              className="bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 text-white shadow-[0_0_18px_rgba(99,102,241,0.5)] hover:shadow-[0_0_28px_rgba(99,102,241,0.75)] transition-all border border-indigo-400/30 rounded-xl h-11 px-5"
            >
              <RefreshCw className="mr-2 h-4 w-4" />
              Force Retrain Models
            </Button>
          </div>

          <div className="grid gap-5 md:grid-cols-3">
            <Card className={cardBase}>
              <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/60 to-transparent" />
              <div className="pointer-events-none absolute -top-12 -right-8 h-32 w-32 rounded-full bg-indigo-500/15 blur-3xl" />
              <CardContent className="relative p-6">
                <p className="text-[10px] uppercase tracking-[0.22em] text-indigo-300 mb-2 font-semibold">
                  Total Logs Captured
                </p>
                <div className="text-4xl font-semibold text-white tabular-nums tracking-tight">
                  {mlops.stats.total_logs_captured}
                </div>
              </CardContent>
            </Card>

            <Card className={cardBase}>
              <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-emerald-400/60 to-transparent" />
              <div className="pointer-events-none absolute -top-12 -right-8 h-32 w-32 rounded-full bg-emerald-500/15 blur-3xl" />
              <CardContent className="relative p-6">
                <p className="text-[10px] uppercase tracking-[0.22em] text-emerald-300 mb-2 font-semibold">
                  GNN Routes
                </p>
                <div className="text-4xl font-semibold text-white tabular-nums tracking-tight">
                  {mlops.stats.gnn_routes}
                </div>
              </CardContent>
            </Card>

            <Card className={cardBase}>
              <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-amber-400/60 to-transparent" />
              <div className="pointer-events-none absolute -top-12 -right-8 h-32 w-32 rounded-full bg-amber-500/15 blur-3xl" />
              <CardContent className="relative p-6">
                <p className="text-[10px] uppercase tracking-[0.22em] text-amber-300 mb-2 font-semibold">
                  Elo Routes
                </p>
                <div className="text-4xl font-semibold text-white tabular-nums tracking-tight">
                  {mlops.stats.elo_routes}
                </div>
              </CardContent>
            </Card>
          </div>

          <Card className={cardBase}>
            <CardHeader className="border-b border-white/[0.06] pb-4">
              <CardTitle className="text-base font-medium flex items-center gap-2 text-white tracking-tight">
                <Server className="h-4 w-4 text-indigo-400" />
                Live Recommendation Logs
                <span className="text-[10px] font-mono uppercase tracking-[0.22em] text-slate-500 ml-2">
                  Phase 1 Data
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead className="text-[10px] text-slate-400 uppercase tracking-[0.18em] bg-white/[0.02] border-b border-white/[0.06]">
                    <tr>
                      <th className="px-6 py-3 font-semibold">Timestamp</th>
                      <th className="px-6 py-3 font-semibold">User</th>
                      <th className="px-6 py-3 font-semibold">Topic</th>
                      <th className="px-6 py-3 font-semibold">Engine</th>
                      <th className="px-6 py-3 font-semibold">Passed</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.04]">
                    {mlops.recent_logs.map((log: any) => (
                      <tr key={log.id} className="hover:bg-white/[0.02] transition-colors">
                        <td className="px-6 py-4 font-mono text-xs text-slate-500">{log.created_at}</td>
                        <td className="px-6 py-4 text-slate-300">{log.user}</td>
                        <td className="px-6 py-4 font-medium text-white">{log.topic}</td>
                        <td className="px-6 py-4">
                          <span className={`inline-flex items-center px-2 py-1 rounded-md text-[10px] uppercase font-semibold tracking-[0.14em] border ${
                            log.engine === 'hierarchical'
                              ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/25 shadow-[0_0_10px_rgba(16,185,129,0.15)]'
                              : 'bg-amber-500/10 text-amber-300 border-amber-500/25 shadow-[0_0_10px_rgba(251,191,36,0.15)]'
                          }`}>
                            {log.engine}
                          </span>
                        </td>
                        <td className="px-6 py-4">
                          {log.actual_result === true ? (
                            <span className="text-emerald-400 font-medium inline-flex items-center gap-1">
                              <CheckCircle2 className="h-3.5 w-3.5" /> Yes
                            </span>
                          ) : log.actual_result === false ? (
                            <span className="text-rose-400 font-medium inline-flex items-center gap-1">
                              <XCircle className="h-3.5 w-3.5" /> No
                            </span>
                          ) : (
                            <span className="text-slate-500 italic text-xs">Pending</span>
                          )}
                        </td>
                      </tr>
                    ))}
                    {mlops.recent_logs.length === 0 && (
                      <tr>
                        <td colSpan={5} className="px-6 py-10 text-center text-slate-500 italic">
                          No telemetry data captured yet. Solve some problems!
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}