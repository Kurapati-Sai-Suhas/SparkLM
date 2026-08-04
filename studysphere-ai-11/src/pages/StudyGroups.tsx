import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardFooter,
  CardContent,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Users,
  LogIn,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
  Shield,
  ArrowRight,
  Globe2,
  Plus,
  Sparkles,
} from "lucide-react";
import api, { groupsAPI } from "@/services/api";

export default function StudyGroups() {
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Pagination State
  const [page, setPage] = useState(1);
  const [hasNext, setHasNext] = useState(false);
  const [hasPrev, setHasPrev] = useState(false);

  // Inputs
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupDesc, setNewGroupDesc] = useState("");
  const [newGroupCapacity, setNewGroupCapacity] = useState(10);
  const [joinCode, setJoinCode] = useState("");

  const fetchGroups = async (pageNum: number) => {
    setLoading(true);
    setError("");
    try {
      const response = await api.get("groups/", { params: { page: pageNum } });
      const data = response.data;
      setGroups(data.results || []);
      setHasNext(!!data.next);
      setHasPrev(!!data.previous);
    } catch (err: any) {
      console.error("Fetch error:", err);
      setError(err.message || "Failed to load groups");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchGroups(page);
  }, [page]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await groupsAPI.create({
        name: newGroupName,
        description: newGroupDesc,
        capacity: newGroupCapacity,
      });
      alert("Group Created! 🎉");
      setNewGroupName("");
      setNewGroupDesc("");
      setNewGroupCapacity(10);
      setPage(1);
      fetchGroups(1);
    } catch (err) {
      alert("Error creating group.");
    }
  };

  const handleJoin = async () => {
    try {
      await groupsAPI.join(joinCode);
      alert("Joined Successfully!");
      setJoinCode("");
      fetchGroups(page);
    } catch (err: any) {
      alert(err.response?.data?.error || "Error joining group");
    }
  };

  // SparkLM premium tokens
  const glassCard =
    "relative overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-2xl " +
    "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]";

  const sleekInput =
    "h-10 bg-white/[0.03] backdrop-blur border-white/[0.08] text-white " +
    "placeholder:text-slate-500 focus-visible:ring-1 focus-visible:ring-indigo-400/60 " +
    "focus-visible:border-indigo-400/50 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.03)] " +
    "transition-all duration-200";

  const primaryGlowBtn =
    "bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 " +
    "text-white border border-indigo-400/30 " +
    "shadow-[0_0_18px_rgba(99,102,241,0.5)] hover:shadow-[0_0_30px_rgba(99,102,241,0.75)] " +
    "transition-all duration-300 font-medium";

  if (loading && groups.length === 0)
    return (
      <div
        data-testid="groups-loading"
        className="flex justify-center items-center h-[60vh]"
      >
        <div className="relative">
          <div className="h-14 w-14 rounded-full border-2 border-white/10" />
          <div className="absolute inset-0 h-14 w-14 rounded-full border-2 border-transparent border-t-indigo-400 animate-spin shadow-[0_0_30px_rgba(99,102,241,0.5)]" />
          <Users className="absolute inset-0 m-auto h-5 w-5 text-indigo-300" />
        </div>
      </div>
    );

  return (
    <div
      data-testid="study-groups-page"
      className="relative space-y-8 p-6 md:p-10 max-w-7xl mx-auto w-full font-sans animate-in fade-in duration-500"
    >
      {/* HERO */}
      <div
        data-testid="groups-hero"
        className="animate-in slide-in-from-bottom-4 fade-in duration-500 relative overflow-hidden rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.04] via-white/[0.02] to-transparent backdrop-blur-2xl p-8 md:p-12"
      >
        {/* Masked grid */}
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

        <div className="relative z-10 max-w-2xl">
          <div className="inline-flex items-center gap-2 rounded-full border border-indigo-400/25 bg-indigo-500/[0.08] backdrop-blur px-3 py-1.5 text-[11px] font-medium text-indigo-200 shadow-[0_0_20px_rgba(99,102,241,0.15)]">
            <Sparkles className="h-3 w-3 text-indigo-300" />
            Collaborative learning
          </div>
          <h1 className="mt-5 text-4xl sm:text-5xl lg:text-6xl font-semibold tracking-tight text-white leading-[1.05]">
            Study{" "}
            <span className="bg-gradient-to-r from-indigo-300 via-indigo-400 to-blue-400 bg-clip-text text-transparent">
              Groups
            </span>
          </h1>
          <p className="mt-4 text-base md:text-lg text-slate-400 leading-relaxed">
            Collaborate with your peers, share study materials, and conquer your exams together.
          </p>
        </div>
      </div>

      {/* ERROR BAR */}
      {error && (
        <div
          data-testid="groups-error"
          className="flex items-center gap-3 rounded-xl border border-rose-500/30 bg-rose-500/[0.08] backdrop-blur-xl px-4 py-3 text-rose-300 shadow-[0_0_20px_rgba(244,63,94,0.15)]"
        >
          <AlertCircle className="w-5 h-5 shrink-0" />
          <span className="font-medium text-sm">{error}</span>
          <Button
            data-testid="groups-error-retry"
            variant="outline"
            size="sm"
            className="ml-auto h-8 border-rose-500/40 bg-white/[0.03] text-rose-200 hover:bg-rose-500/10 hover:text-rose-100 hover:border-rose-400/60"
            onClick={() => fetchGroups(page)}
          >
            Retry
          </Button>
        </div>
      )}

      <div className="grid gap-8 md:grid-cols-12">
        {/* LEFT COLUMN — Control Panel */}
        <div className="md:col-span-4 lg:col-span-3">
          <Card
            data-testid="group-menu-card"
            className={`sticky top-6 ${glassCard}`}
          >
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/70 to-transparent" />

            <CardHeader className="border-b border-white/[0.06] pb-4">
              <CardTitle className="text-base font-medium text-white flex items-center gap-2 tracking-tight">
                <span className="h-1.5 w-1.5 rounded-full bg-indigo-400 shadow-[0_0_10px_rgba(99,102,241,1)]" />
                Group Menu
              </CardTitle>
            </CardHeader>

            <CardContent className="pt-6">
              <Tabs defaultValue="join">
                {/* Premium segmented control */}
                <TabsList
                  data-testid="group-menu-tabs"
                  className="relative grid w-full grid-cols-2 mb-6 h-10 p-1 rounded-lg bg-white/[0.03] backdrop-blur border border-white/[0.06]"
                >
                  <TabsTrigger
                    data-testid="tab-join"
                    value="join"
                    className="relative rounded-md text-sm font-medium text-slate-400
                      data-[state=active]:text-white
                      data-[state=active]:bg-white/[0.05]
                      data-[state=active]:shadow-[0_0_0_1px_rgba(255,255,255,0.06),0_0_18px_rgba(99,102,241,0.3)]
                      transition-all duration-300"
                  >
                    <LogIn className="w-3.5 h-3.5 mr-1.5" />
                    Join
                  </TabsTrigger>
                  <TabsTrigger
                    data-testid="tab-create"
                    value="create"
                    className="relative rounded-md text-sm font-medium text-slate-400
                      data-[state=active]:text-white
                      data-[state=active]:bg-white/[0.05]
                      data-[state=active]:shadow-[0_0_0_1px_rgba(255,255,255,0.06),0_0_18px_rgba(99,102,241,0.3)]
                      transition-all duration-300"
                  >
                    <Plus className="w-3.5 h-3.5 mr-1.5" />
                    Create
                  </TabsTrigger>
                </TabsList>

                {/* JOIN */}
                <TabsContent
                  value="join"
                  className="space-y-4 animate-in fade-in slide-in-from-left-2 duration-300"
                >
                  <div className="space-y-2">
                    <Label className="text-[10px] uppercase tracking-[0.22em] text-slate-400 font-semibold">
                      Access Code
                    </Label>
                    <div className="flex gap-2">
                      <Input
                        data-testid="join-code-input"
                        placeholder="e.g. CS101"
                        value={joinCode}
                        onChange={(e) => setJoinCode(e.target.value)}
                        className={`${sleekInput} font-mono tracking-wider`}
                      />
                      <Button
                        data-testid="join-submit-btn"
                        onClick={handleJoin}
                        className={`h-10 px-3 ${primaryGlowBtn}`}
                      >
                        <LogIn className="w-4 h-4" />
                      </Button>
                    </div>
                    <p className="text-xs text-slate-500">
                      Ask the group owner for an invite code.
                    </p>
                  </div>
                </TabsContent>

                {/* CREATE */}
                <TabsContent
                  value="create"
                  className="space-y-4 animate-in fade-in slide-in-from-right-2 duration-300"
                >
                  <form
                    data-testid="create-group-form"
                    onSubmit={handleCreate}
                    className="space-y-4"
                  >
                    <div className="space-y-1.5">
                      <Label className="text-[10px] uppercase tracking-[0.22em] text-slate-400 font-semibold">
                        Group Name
                      </Label>
                      <Input
                        data-testid="create-name-input"
                        value={newGroupName}
                        onChange={(e) => setNewGroupName(e.target.value)}
                        placeholder="e.g. Data Structures"
                        required
                        className={sleekInput}
                      />
                    </div>

                    <div className="space-y-1.5">
                      <Label className="text-[10px] uppercase tracking-[0.22em] text-slate-400 font-semibold">
                        Description
                      </Label>
                      <Input
                        data-testid="create-desc-input"
                        value={newGroupDesc}
                        onChange={(e) => setNewGroupDesc(e.target.value)}
                        placeholder="What's this group about?"
                        required
                        className={sleekInput}
                      />
                    </div>

                    {/* The "Secret Code" input is gone: join codes are now
                        generated server-side (M4 WP5). Typed codes were
                        short and memorable — "pass123" was the placeholder —
                        and a guessed code grants full group membership. The
                        generated code is shown on the group card as soon as
                        the group exists, so sharing it is unchanged. */}
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <Label className="text-[10px] uppercase tracking-[0.22em] text-slate-400 font-semibold">
                          Capacity
                        </Label>
                        <Input
                          data-testid="create-capacity-input"
                          type="number"
                          min={1}
                          value={newGroupCapacity}
                          onChange={(e) => setNewGroupCapacity(Number(e.target.value))}
                          required
                          className={`${sleekInput} tabular-nums`}
                        />
                      </div>
                    </div>

                    <Button
                      data-testid="create-submit-btn"
                      type="submit"
                      className={`w-full h-10 rounded-lg ${primaryGlowBtn}`}
                    >
                      <Plus className="w-4 h-4 mr-1.5" />
                      Create Group
                    </Button>
                  </form>
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>
        </div>

        {/* RIGHT COLUMN — Public Groups Grid */}
        <div className="md:col-span-8 lg:col-span-9 flex flex-col">
          {/* Section header */}
          <div className="mb-6 flex items-end justify-between gap-4 border-b border-white/[0.06] pb-4">
            <div>
              <h2 className="text-2xl font-semibold tracking-tight text-white flex items-center gap-2">
                <Globe2 className="h-5 w-5 text-indigo-400" />
                Explore Public Groups
              </h2>
              <p className="text-sm text-slate-400 mt-1">
                Browse every group on the platform — join one with an access code.
              </p>
            </div>
            <div className="hidden md:inline-flex items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.03] backdrop-blur px-3 py-1 text-xs text-slate-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.9)]" />
              <span className="tabular-nums font-medium text-white">{groups.length}</span>
              <span>visible</span>
            </div>
          </div>

          {/* Grid */}
          <div
            data-testid="groups-grid"
            className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3"
          >
            {groups.length === 0 && !error ? (
              <div
                data-testid="groups-empty-state"
                className="col-span-full flex flex-col items-center justify-center p-14 rounded-2xl border border-dashed border-white/10 bg-white/[0.02] backdrop-blur-2xl"
              >
                <div className="h-16 w-16 rounded-2xl border border-white/10 bg-white/[0.03] flex items-center justify-center mb-4">
                  <Users className="h-6 w-6 text-slate-500" />
                </div>
                <p className="text-base font-medium text-white">No groups found</p>
                <p className="text-sm text-slate-400 mt-1">
                  Create one or join with an access code from the menu.
                </p>
              </div>
            ) : (
              groups.map((group: any, idx: number) => {
                const membersCount = group.members ? group.members.length : 0;
                const capacity = group.capacity || 10;
                const fillPct = Math.min(100, (membersCount / capacity) * 100);

                return (
                  <Link
                    to={`/groups/${group.id}`}
                    key={group.id}
                    data-testid={`group-card-${group.id}`}
                    className="group relative animate-in fade-in slide-in-from-bottom-2 duration-500"
                    style={{ animationDelay: `${idx * 50}ms` }}
                  >
                    {/* Outer aurora glow on hover */}
                    <div className="absolute -inset-0.5 rounded-2xl bg-gradient-to-br from-indigo-500/0 via-indigo-500/0 to-blue-500/0 group-hover:from-indigo-500/40 group-hover:via-indigo-400/20 group-hover:to-blue-500/40 blur-lg opacity-0 group-hover:opacity-100 transition-opacity duration-500" />

                    <Card
                      className={`relative h-full ${glassCard} transition-all duration-300
                        group-hover:border-indigo-400/40
                        group-hover:-translate-y-1
                        group-hover:shadow-[0_20px_60px_-15px_rgba(99,102,241,0.4)]`}
                    >
                      {/* Top accent line */}
                      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/0 to-transparent group-hover:via-indigo-400/80 transition-all duration-500" />

                      <CardHeader className="pb-3">
                        <div className="flex items-start justify-between gap-2">
                          <CardTitle className="truncate text-lg font-semibold text-white group-hover:text-indigo-200 transition-colors tracking-tight">
                            {group.name}
                          </CardTitle>
                          <div className="shrink-0 inline-flex items-center gap-1 rounded-full border border-white/[0.08] bg-white/[0.03] backdrop-blur px-2 py-0.5 text-[10px] font-medium text-slate-400">
                            <Users className="w-3 h-3" />
                            <span className="tabular-nums text-white font-semibold">{membersCount}</span>
                            <span>/ {capacity}</span>
                          </div>
                        </div>
                        <CardDescription className="line-clamp-2 mt-2 h-10 text-sm text-slate-400 leading-relaxed">
                          {group.description}
                        </CardDescription>
                      </CardHeader>

                      <CardContent className="pb-3">
                        {/* Members fill bar */}
                        <div className="h-1 w-full rounded-full bg-white/[0.04] overflow-hidden">
                          <div
                            className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-indigo-400 shadow-[0_0_10px_rgba(99,102,241,0.7)] transition-[width] duration-500"
                            style={{ width: `${fillPct}%` }}
                          />
                        </div>
                      </CardContent>

                      <CardFooter className="pt-3 pb-4 mt-auto flex justify-between items-center border-t border-white/[0.06]">
                        {/* High-tech Access Code Pill */}
                        <div
                          data-testid={`group-access-code-${group.id}`}
                          className="inline-flex items-center gap-2 rounded-full border border-emerald-400/30 bg-emerald-500/[0.08] backdrop-blur px-2.5 py-1 text-xs font-mono tracking-wider text-emerald-300 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.04),0_0_12px_rgba(16,185,129,0.2)]"
                        >
                          <span className="relative flex h-1.5 w-1.5">
                            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
                            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
                          </span>
                          <Shield className="w-3 h-3" />
                          {group.join_code}
                        </div>

                        <div className="h-8 w-8 rounded-lg border border-white/[0.06] bg-white/[0.03] backdrop-blur text-slate-500 flex items-center justify-center opacity-0 -translate-x-2 group-hover:opacity-100 group-hover:translate-x-0 group-hover:border-indigo-400/50 group-hover:bg-indigo-500/10 group-hover:text-indigo-300 group-hover:shadow-[0_0_15px_rgba(99,102,241,0.5)] transition-all duration-300">
                          <ArrowRight className="w-4 h-4" />
                        </div>
                      </CardFooter>
                    </Card>
                  </Link>
                );
              })
            )}
          </div>

          {/* PAGINATION */}
          <div
            data-testid="pagination-controls"
            className="flex items-center justify-center gap-3 mt-10"
          >
            <Button
              data-testid="pagination-prev"
              variant="outline"
              className="h-10 rounded-lg border-white/[0.08] bg-white/[0.03] backdrop-blur text-slate-300 hover:bg-white/[0.06] hover:border-indigo-400/50 hover:text-indigo-200 disabled:opacity-40 disabled:hover:border-white/[0.08] disabled:hover:text-slate-300 disabled:hover:bg-white/[0.03] transition-all"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={!hasPrev}
            >
              <ChevronLeft className="w-4 h-4 mr-1.5" />
              Previous
            </Button>

            <div
              data-testid="pagination-current-page"
              className="inline-flex items-center gap-2 rounded-lg border border-white/[0.08] bg-white/[0.03] backdrop-blur px-4 py-2 text-sm font-medium text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]"
            >
              <span className="text-[10px] uppercase tracking-[0.22em] text-slate-400 font-semibold">
                Page
              </span>
              <span className="tabular-nums text-indigo-300 font-semibold">{page}</span>
            </div>

            <Button
              data-testid="pagination-next"
              variant="outline"
              className="h-10 rounded-lg border-white/[0.08] bg-white/[0.03] backdrop-blur text-slate-300 hover:bg-white/[0.06] hover:border-indigo-400/50 hover:text-indigo-200 disabled:opacity-40 disabled:hover:border-white/[0.08] disabled:hover:text-slate-300 disabled:hover:bg-white/[0.03] transition-all"
              onClick={() => setPage((p) => p + 1)}
              disabled={!hasNext}
            >
              Next
              <ChevronRight className="w-4 h-4 ml-1.5" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}