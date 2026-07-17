import { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  CheckCircle2,
  BookOpen,
  AlertTriangle,
  Lock,
  Cpu,
  Sparkles,
  ArrowRight,
  Target,
  TrendingUp,
} from "lucide-react";

type NodeStatus = "mastered" | "learning" | "struggling" | "locked";

interface TopicNode {
  id: string;
  name: string;
  mastery: number;
  status: NodeStatus;
  column: number; // 0..N — defines DAG depth
}

// Curriculum comes exclusively from GET /api/ai/mastery-map/ — no mock data.

const STATUS_META: Record<
  NodeStatus,
  {
    label: string;
    ring: string;
    text: string;
    bg: string;
    glow: string;
    icon: React.ComponentType<{ className?: string }>;
    progress: string;
  }
> = {
  mastered: {
    label: "Mastered",
    ring: "border-emerald-500/40",
    text: "text-emerald-300",
    bg: "bg-emerald-500/10",
    glow: "hover:shadow-[0_0_20px_rgba(16,185,129,0.35)]",
    icon: CheckCircle2,
    progress: "[&>div]:bg-emerald-400",
  },
  learning: {
    label: "Learning",
    ring: "border-amber-500/40",
    text: "text-amber-300",
    bg: "bg-amber-500/10",
    glow: "hover:shadow-[0_0_20px_rgba(245,158,11,0.35)]",
    // Deliberately NOT a spinner: a fresh account has every root topic in
    // "learning" at 0%, and a wall of spinning loaders reads as the page
    // being stuck loading forever.
    icon: BookOpen,
    progress: "[&>div]:bg-amber-400",
  },
  struggling: {
    label: "Struggling",
    ring: "border-rose-500/40",
    text: "text-rose-300",
    bg: "bg-rose-500/10",
    glow: "hover:shadow-[0_0_20px_rgba(244,63,94,0.35)]",
    icon: AlertTriangle,
    progress: "[&>div]:bg-rose-400",
  },
  locked: {
    label: "Locked",
    ring: "border-border/60",
    text: "text-muted-foreground",
    bg: "bg-muted/20",
    glow: "",
    icon: Lock,
    progress: "[&>div]:bg-muted-foreground/40",
  },
};

export default function LearningPathVisualizer({ onStartTopic }: { onStartTopic?: (topic: string) => void }) {
  const [searchParams] = useSearchParams();
  // Default must name the PORTAL (DSA curriculum), not a topic: a
  // non-portal subject makes the backend fall back to an all-topics
  // graph, which pollutes the DAG with stray CSV topics (Design,
  // Enumeration, ...) and bloats the payload.
  const subject = searchParams.get('topic') || 'DSA';

  const [curriculum, setCurriculum] = useState<TopicNode[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [routerInfo, setRouterInfo] = useState<{
    route: string;
    runs_z: number;
    avg_acc: number;
    sample_size: number;
  } | null>(null);

  useEffect(() => {
    // Real router telemetry for the "Why You're Here" panel — this panel
    // previously showed static placeholder text, which misrepresented the
    // actual routing decision.
    const token = localStorage.getItem('authToken') || localStorage.getItem('access');
    fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/review/queue/`, {
      headers: { Authorization: `Bearer ${token}` }
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { if (data?.router) setRouterInfo(data.router); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const fetchMasteryMap = async () => {
      try {
        const token = localStorage.getItem('authToken') || localStorage.getItem('access');
        const res = await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/ai/mastery-map/?subject=${subject}`, {
          headers: { Authorization: `Bearer ${token}` }
        });
        if (res.ok) {
          const data = await res.json();
          if (data && Object.keys(data).length > 0) {
             // Real per-topic accuracy and DAG depth come from the backend.
             const nodes: TopicNode[] = Object.keys(data).map((key, index) => {
               const nodeData = data[key];
               const mastery = Math.round(nodeData.accuracy_pct ?? 0);
               let status: NodeStatus = 'locked';
               if (nodeData.mastered) status = 'mastered';
               else if (nodeData.unlocked) status = mastery > 0 && mastery < 40 ? 'struggling' : 'learning';

               return {
                 id: key,
                 name: key,
                 mastery,
                 status,
                 column: nodeData.depth ?? index % 4,
               };
             });
             setCurriculum(nodes);
             setActiveId(nodes.find(n => n.status === 'learning')?.id || nodes[0].id);
          }
        }
      } catch (e) {
        console.error("Failed to fetch mastery map", e);
      } finally {
        setLoading(false);
      }
    };
    fetchMasteryMap();
  }, [subject]);

  const columns = Array.from(new Set(curriculum.map((n) => n.column))).sort();
  const activeNode = curriculum.find((n) => n.id === activeId) || curriculum[0];

  // First render always arrives here with an empty curriculum (the fetch
  // hasn't resolved yet). Without this guard, the router panel below reads
  // activeNode.name on undefined and blank-screens the whole route.
  if (loading) {
    return (
      <div className="relative min-h-screen bg-background text-foreground p-6 md:p-8 flex items-center justify-center">
        <p className="text-sm text-muted-foreground animate-pulse">
          Loading your mastery graph…
        </p>
      </div>
    );
  }

  if (!loading && curriculum.length === 0) {
    return (
      <div className="relative min-h-screen bg-background text-foreground p-6 md:p-8 flex items-center justify-center">
        <p className="text-sm text-muted-foreground">
          Curriculum graph unavailable — seed the DSA curriculum (manage.py seed_dsa_dag) and refresh.
        </p>
      </div>
    );
  }

  return (
    <TooltipProvider delayDuration={150}>
      <div className="relative min-h-screen bg-background text-foreground p-6 md:p-8">
        <div className="max-w-7xl mx-auto">
          {/* Header */}
          <header className="mb-8">
            <div className="inline-flex items-center gap-2 rounded-full border border-border/60 bg-card/40 backdrop-blur-md px-3 py-1 text-[11px] font-medium text-muted-foreground">
              <Sparkles className="h-3 w-3 text-primary" />
              GNN-driven curriculum graph
            </div>
            <h1 className="mt-4 text-3xl md:text-4xl font-semibold tracking-tight">
              Learning Path
            </h1>
            <p className="mt-1.5 text-sm text-muted-foreground">
              Your mastery DAG, updated in real time by the Hybrid ML Router.
            </p>
          </header>

          <div className="grid gap-6 lg:grid-cols-12">
            {/* ==================== GRAPH ==================== */}
            <section className="lg:col-span-8">
              <Card className="bg-card/40 backdrop-blur-md border-border/60">
                <CardHeader className="border-b border-border/60 pb-4">
                  <CardTitle className="text-base font-medium flex items-center gap-2">
                    <span className="h-1.5 w-1.5 rounded-full bg-primary shadow-[0_0_8px_rgba(59,130,246,0.8)]" />
                    Curriculum DAG
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-6 overflow-x-auto">
                  <div className="flex items-stretch gap-6 min-w-max">
                    {columns.map((col, colIdx) => {
                      const nodes = curriculum.filter((n) => n.column === col);
                      return (
                        <div key={col} className="flex items-center gap-6">
                          {/* Column of nodes */}
                          <div className="flex flex-col gap-4 w-48">
                            <span className="text-[10px] font-mono uppercase tracking-[0.22em] text-muted-foreground">
                              Tier {col + 1}
                            </span>
                            {nodes.map((node) => {
                              const meta = STATUS_META[node.status];
                              const Icon = meta.icon;
                              const isActive = node.id === activeId;
                              const isRecommended = node.id === activeId;

                              return (
                                <Tooltip key={node.id}>
                                  <TooltipTrigger asChild>
                                    <button
                                      onClick={() => setActiveId(node.id)}
                                      className={`group relative text-left rounded-xl border backdrop-blur-md p-4
                                        bg-card/40 transition-all duration-200
                                        ${meta.ring} ${meta.glow}
                                        ${
                                          isActive
                                            ? "ring-1 ring-primary/60 shadow-[0_0_20px_rgba(59,130,246,0.35)]"
                                            : ""
                                        }
                                        hover:-translate-y-0.5`}
                                    >
                                      {isRecommended && (
                                        <span className="absolute -top-2 -right-2 inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/15 px-2 py-0.5 text-[9px] font-medium uppercase tracking-[0.18em] text-primary shadow-[0_0_12px_rgba(59,130,246,0.4)]">
                                          <Target className="h-2.5 w-2.5" />
                                          Next
                                        </span>
                                      )}

                                      <div className="flex items-center justify-between mb-2">
                                        <div
                                          className={`h-7 w-7 rounded-md flex items-center justify-center ${meta.bg} ${meta.text}`}
                                        >
                                          <Icon className="h-3.5 w-3.5" />
                                        </div>
                                        <Badge
                                          variant="outline"
                                          className={`text-[10px] font-mono ${meta.ring} ${meta.text} bg-transparent`}
                                        >
                                          {meta.label}
                                        </Badge>
                                      </div>

                                      <p className="text-sm font-medium text-foreground truncate">
                                        {node.name}
                                      </p>

                                      <div className="mt-3 space-y-1.5">
                                        <div className="flex items-center justify-between text-[11px]">
                                          <span className="text-muted-foreground">
                                            Mastery
                                          </span>
                                          <span
                                            className={`font-mono tabular-nums ${meta.text}`}
                                          >
                                            {node.mastery}%
                                          </span>
                                        </div>
                                        <Progress
                                          value={node.mastery}
                                          className={`h-1 bg-muted/40 ${meta.progress}`}
                                        />
                                      </div>
                                    </button>
                                  </TooltipTrigger>
                                  <TooltipContent
                                    side="top"
                                    className="bg-card/95 backdrop-blur-md border-border/60 text-xs"
                                  >
                                    {node.status === "locked"
                                      ? "Unlock by completing prerequisites"
                                      : `${meta.label} · ${node.mastery}% mastery`}
                                  </TooltipContent>
                                </Tooltip>
                              );
                            })}
                          </div>

                          {/* Connector */}
                          {colIdx < columns.length - 1 && (
                            <div className="flex flex-col justify-center self-stretch">
                              <div className="relative h-12 w-12 flex items-center justify-center">
                                <div className="absolute inset-y-1/2 left-0 right-0 h-px bg-gradient-to-r from-border via-primary/40 to-border" />
                                <ArrowRight className="relative h-4 w-4 text-primary drop-shadow-[0_0_6px_rgba(59,130,246,0.6)]" />
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  {/* Legend */}
                  <div className="mt-8 pt-5 border-t border-border/60 flex flex-wrap items-center gap-4">
                    {(Object.keys(STATUS_META) as NodeStatus[]).map((s) => {
                      const m = STATUS_META[s];
                      return (
                        <div
                          key={s}
                          className="inline-flex items-center gap-2 text-[11px] text-muted-foreground"
                        >
                          <span
                            className={`h-2 w-2 rounded-full ${m.bg} ${m.text}`}
                          />
                          <span className={m.text}>{m.label}</span>
                        </div>
                      );
                    })}
                  </div>
                </CardContent>
              </Card>
            </section>

            {/* ==================== ROUTER PANEL ==================== */}
            <aside className="lg:col-span-4">
              <Card className="bg-card/40 backdrop-blur-md border-border/60 sticky top-6 overflow-hidden">
                <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/60 to-transparent" />
                <div className="pointer-events-none absolute -top-24 -right-24 h-48 w-48 rounded-full bg-primary/15 blur-3xl" />

                <CardHeader className="border-b border-border/60 pb-4 relative">
                  <CardTitle className="text-[11px] font-medium tracking-[0.22em] uppercase text-primary flex items-center gap-2">
                    <span className="relative flex h-2 w-2">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />
                      <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
                    </span>
                    <Cpu className="h-3.5 w-3.5" />
                    Why You're Here
                  </CardTitle>
                </CardHeader>

                <CardContent className="pt-5 space-y-5 relative">
                  <p className="text-sm leading-relaxed text-muted-foreground">
                    <span className="text-foreground font-medium">
                      Hybrid ML Router Decision:
                    </span>{" "}
                    {routerInfo ? (
                      <>
                        Routed via{" "}
                        <span className="text-primary font-mono">
                          {routerInfo.route === "flat" ? "Elo practice" : "Curriculum DAG"}
                        </span>{" "}
                        — recent results look{" "}
                        {routerInfo.runs_z > 1.96
                          ? "erratic (oscillating pass/fail)"
                          : routerInfo.runs_z < -1.96
                          ? "streaky (breakthrough pattern)"
                          : "steady"}{" "}
                        over your last {routerInfo.sample_size} submissions.
                      </>
                    ) : (
                      <>Computing your route from recent submissions…</>
                    )}
                  </p>

                  <div className="rounded-lg border border-border/60 bg-background/40 backdrop-blur p-3.5">
                    <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.18em] text-muted-foreground mb-2">
                      <Target className="h-3 w-3 text-primary" />
                      Next Recommended Topic
                    </div>
                    <p className="text-base font-semibold text-foreground">
                      {activeNode.name}
                    </p>
                    <div className="mt-2 flex items-center justify-between text-[11px]">
                      <span className="text-muted-foreground">
                        Current mastery
                      </span>
                      <span
                        className={`font-mono tabular-nums ${STATUS_META[activeNode.status].text}`}
                      >
                        {activeNode.mastery}%
                      </span>
                    </div>
                    <Progress
                      value={activeNode.mastery}
                      className={`h-1 mt-1.5 bg-muted/40 ${STATUS_META[activeNode.status].progress}`}
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-2">
                    <div className="rounded-lg border border-border/60 bg-background/40 backdrop-blur p-3">
                      <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground mb-1">
                        Router
                      </div>
                      <div className="text-sm font-mono text-primary">
                        {routerInfo
                          ? routerInfo.route === "flat"
                            ? "Elo"
                            : "DAG"
                          : "—"}
                      </div>
                    </div>
                    <div className="rounded-lg border border-border/60 bg-background/40 backdrop-blur p-3">
                      <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground mb-1">
                        Streakiness
                      </div>
                      <div className="text-sm font-mono text-amber-300 flex items-center gap-1">
                        <TrendingUp className="h-3 w-3" />
                        {routerInfo
                          ? `z=${routerInfo.runs_z.toFixed(2)}`
                          : "—"}
                      </div>
                    </div>
                  </div>

                  <button
                    onClick={() =>
                      activeNode.status !== "locked" &&
                      onStartTopic &&
                      onStartTopic(activeNode.name)
                    }
                    disabled={activeNode.status === "locked"}
                    className="w-full h-10 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 shadow-[0_0_15px_rgba(59,130,246,0.4)] hover:shadow-[0_0_25px_rgba(59,130,246,0.6)] transition-all text-sm font-medium inline-flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none disabled:hover:bg-primary">
                    {activeNode.status === "locked" ? (
                      <>
                        <Lock className="h-4 w-4" />
                        Complete prerequisites to unlock
                      </>
                    ) : (
                      <>
                        Start {activeNode.name}
                        <ArrowRight className="h-4 w-4" />
                      </>
                    )}
                  </button>
                </CardContent>
              </Card>
            </aside>
          </div>
        </div>
      </div>
    </TooltipProvider>
  );
}
