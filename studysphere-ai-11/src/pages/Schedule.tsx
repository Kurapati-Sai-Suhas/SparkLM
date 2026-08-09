import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Calendar as CalendarIcon, Plus, Sparkles, Clock, CheckCircle, X } from "lucide-react";
import { Calendar } from "@/components/ui/calendar";
import { useState, useEffect } from "react";
import { format } from "date-fns";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { scheduleAPI } from "@/services/api";

export default function Schedule() {
  const [date, setDate] = useState<Date | undefined>(new Date());
  const [events, setEvents] = useState<any[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newTime, setNewTime] = useState("");

  // Was a raw fetch to the RELATIVE path "/api/schedule/". On Vercel that
  // resolved against the SPA's own origin, where the catch-all rewrite in
  // vercel.json serves index.html with HTTP 200 — so this parsed the app's
  // own HTML as JSON, threw, and was swallowed. The page has never loaded
  // real data in production.
  //
  // Going through the shared client fixes the origin (baseURL) and adds the
  // auth header, the CSRF sentinel, and the 401 refresh-and-retry
  // interceptor, none of which a raw fetch received.
  useEffect(() => {
    scheduleAPI.getSchedule()
      .then(({ data }) => {
        if (Array.isArray(data)) setEvents(data);
      })
      .catch(console.error);
  }, []);

  const handleAddSession = () => {
    if (!date || !newTime || !newTitle) {
      toast.error("Please select date, time and enter title");
      return;
    }
    const dateStr = format(date, "yyyy-MM-dd");
    const start_time = `${dateStr}T${newTime}:00`;

    scheduleAPI.createEvent({ title: newTitle, start_time })
      .then(({ data }) => {
        if (data.status === "success") {
          toast.success("Session scheduled!");
          setEvents([...events, { id: data.id, title: newTitle, start_time }]);
          setShowForm(false);
        }
      })
      // A catch is REQUIRED here, not optional. `fetch` resolves on any HTTP
      // status, so the old code silently did nothing on a failure; axios
      // rejects on non-2xx, and without this the rejection would be
      // unhandled. The user now learns the session was not saved instead of
      // watching the form sit there.
      .catch(() => toast.error("Could not schedule that session."));
  };

  return (
    <div
      data-testid="schedule-page"
      className="relative min-h-screen bg-gradient-to-br from-[#0a0f1e] via-[#08091a] to-[#050612] -m-6 p-6 md:p-10 animate-in fade-in duration-500"
    >
      {/* Ambient glows */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-40 left-1/4 h-[420px] w-[420px] rounded-full bg-indigo-600/12 blur-[130px]" />
        <div className="absolute top-1/3 right-0 h-80 w-80 rounded-full bg-violet-500/10 blur-[130px]" />
        <div className="absolute bottom-0 left-0 h-72 w-72 rounded-full bg-blue-500/8 blur-[120px]" />
      </div>

      <div className="relative max-w-7xl mx-auto w-full space-y-8">
        {/* HERO HEADER */}
        <div
          data-testid="schedule-hero"
          className="relative overflow-hidden flex items-center justify-between gap-4 rounded-2xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-2xl p-6 md:p-8 shadow-[0_0_60px_rgba(99,102,241,0.08)] animate-in slide-in-from-bottom-4 fade-in duration-500"
        >
          {/* Decorative grid */}
          <div
            className="absolute inset-0 opacity-[0.035] pointer-events-none"
            style={{
              backgroundImage:
                "linear-gradient(to right, white 1px, transparent 1px), linear-gradient(to bottom, white 1px, transparent 1px)",
              backgroundSize: "32px 32px",
            }}
          />
          <div className="absolute -top-24 -right-24 h-72 w-72 rounded-full bg-indigo-500/20 blur-3xl pointer-events-none" />
          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/40 to-transparent" />

          <div className="relative">
            <div className="inline-flex items-center gap-1.5 text-[10px] font-bold text-indigo-300 uppercase tracking-[0.25em] bg-indigo-500/10 border border-indigo-400/20 px-2.5 py-1 rounded-md">
              <Sparkles className="h-3 w-3" /> Plan your week
            </div>
            <h1 className="mt-3 text-4xl sm:text-5xl font-bold tracking-tight bg-gradient-to-r from-white via-indigo-100 to-violet-200 bg-clip-text text-transparent">
              Schedule
            </h1>
            <p className="mt-2 text-sm md:text-base text-slate-400">
              Manage your study sessions in one immersive workspace.
            </p>
          </div>

          <Button
            data-testid="new-session-btn"
            onClick={() => setShowForm(true)}
            className="relative h-11 px-5 rounded-xl bg-gradient-to-r from-indigo-500 to-violet-600 hover:from-indigo-400 hover:to-violet-500 text-white shadow-[0_0_25px_rgba(99,102,241,0.45)] hover:shadow-[0_0_35px_rgba(99,102,241,0.65)] transition-all font-semibold"
          >
            <Plus className="h-4 w-4 mr-2" /> New Session
          </Button>
        </div>

        <div className="grid gap-6 lg:grid-cols-3">
          {/* CALENDAR */}
          <Card
            data-testid="calendar-card"
            className="lg:col-span-1 h-fit relative overflow-hidden bg-white/[0.02] backdrop-blur-2xl border-white/[0.06]"
          >
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/50 to-transparent" />
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(99,102,241,0.06),transparent_60%)] pointer-events-none" />

            <div className="relative border-b border-white/[0.06] px-6 py-4 flex items-center gap-3">
              <div className="h-9 w-9 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center shadow-[0_0_20px_rgba(99,102,241,0.4)]">
                <CalendarIcon className="h-4 w-4 text-white" />
              </div>
              <div>
                <h3 className="text-white font-semibold text-sm">Calendar</h3>
                <p className="text-[10px] text-slate-500 uppercase tracking-widest">
                  {date ? format(date, "MMMM yyyy") : "Select a date"}
                </p>
              </div>
            </div>

            <CardContent className="relative pt-5">
              <Calendar
                mode="single"
                selected={date}
                onSelect={setDate}
                className="rounded-xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-xl mx-auto p-3 text-slate-200"
                classNames={{
                  months: "flex flex-col space-y-3",
                  month: "space-y-3",
                  caption: "flex justify-center pt-1 relative items-center text-white font-semibold",
                  caption_label: "text-sm font-semibold text-white",
                  nav: "space-x-1 flex items-center",
                  nav_button: "h-7 w-7 bg-white/[0.03] border border-white/[0.08] rounded-lg text-slate-300 hover:bg-indigo-500/20 hover:text-white hover:border-indigo-400/30 transition-all",
                  nav_button_previous: "absolute left-1",
                  nav_button_next: "absolute right-1",
                  table: "w-full border-collapse space-y-1",
                  head_row: "flex",
                  head_cell: "text-slate-500 rounded-md w-9 font-semibold text-[10px] uppercase tracking-widest",
                  row: "flex w-full mt-1.5",
                  cell: "h-9 w-9 text-center text-sm p-0 relative focus-within:relative focus-within:z-20",
                  day: "h-9 w-9 p-0 font-medium text-slate-300 hover:bg-white/[0.05] hover:text-white rounded-lg transition-all",
                  day_selected: "bg-gradient-to-br from-indigo-500 to-violet-600 text-white hover:from-indigo-400 hover:to-violet-500 hover:text-white shadow-[0_0_15px_rgba(99,102,241,0.5)] font-semibold",
                  day_today: "border border-indigo-400/40 text-indigo-200 font-semibold",
                  day_outside: "text-slate-600 opacity-40",
                  day_disabled: "text-slate-700 opacity-30",
                }}
              />

              {date && (
                <div className="mt-4 pt-4 border-t border-white/[0.05] flex items-center gap-2 text-xs">
                  <span className="h-1.5 w-1.5 rounded-full bg-indigo-400 shadow-[0_0_8px_rgba(99,102,241,0.8)]" />
                  <span className="text-slate-500 uppercase tracking-widest text-[10px] font-semibold">Selected</span>
                  <span className="text-white font-semibold ml-auto">{format(date, "EEE, MMM d")}</span>
                </div>
              )}
            </CardContent>
          </Card>

          {/* EVENTS */}
          <Card
            data-testid="events-card"
            className="lg:col-span-2 min-h-[400px] relative overflow-hidden bg-white/[0.02] backdrop-blur-2xl border-white/[0.06]"
          >
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-violet-400/50 to-transparent" />
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(139,92,246,0.06),transparent_60%)] pointer-events-none" />

            <div className="relative border-b border-white/[0.06] px-6 py-4 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="h-9 w-9 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center shadow-[0_0_20px_rgba(139,92,246,0.4)]">
                  <Clock className="h-4 w-4 text-white" />
                </div>
                <div>
                  <h3 className="text-white font-semibold text-sm">Upcoming Events</h3>
                  <p className="text-[10px] text-slate-500 uppercase tracking-widest">
                    {events.length} scheduled
                  </p>
                </div>
              </div>
              {events.length > 0 && (
                <span className="flex items-center gap-1.5 bg-white/[0.03] border border-white/[0.06] rounded-full px-3 py-1">
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]"></span>
                  </span>
                  <span className="text-[10px] text-emerald-300 uppercase tracking-widest font-semibold">Live</span>
                </span>
              )}
            </div>

            <CardContent className="relative pt-6">
              {showForm ? (
                <div className="space-y-5 animate-in fade-in slide-in-from-top-2 duration-300">
                  <div className="flex items-center gap-3 pb-3 border-b border-white/[0.05]">
                    <div className="h-9 w-9 rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center shadow-[0_0_20px_rgba(99,102,241,0.4)]">
                      <Plus className="h-4 w-4 text-white" />
                    </div>
                    <div>
                      <h4 className="text-white font-semibold text-sm">New Study Session</h4>
                      <p className="text-[10px] text-slate-500 uppercase tracking-widest">
                        {date ? format(date, "EEE, MMM d") : "Pick a date on the calendar"}
                      </p>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Session Title</label>
                    <Input
                      value={newTitle}
                      onChange={e => setNewTitle(e.target.value)}
                      placeholder="e.g. Master DP Algorithms"
                      className="h-11 bg-white/[0.02] border-white/[0.08] text-white placeholder:text-slate-500 focus-visible:ring-indigo-400/40 focus-visible:border-indigo-400/40 transition-all"
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Time</label>
                    <Input
                      type="time"
                      value={newTime}
                      onChange={e => setNewTime(e.target.value)}
                      className="h-11 bg-white/[0.02] border-white/[0.08] text-white focus-visible:ring-indigo-400/40 focus-visible:border-indigo-400/40 transition-all [color-scheme:dark]"
                    />
                  </div>
                  <div className="flex gap-2 pt-2">
                    <Button
                      onClick={handleAddSession}
                      className="h-11 px-5 bg-gradient-to-r from-indigo-500 to-violet-600 hover:from-indigo-400 hover:to-violet-500 text-white font-semibold shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:shadow-[0_0_35px_rgba(99,102,241,0.6)] transition-all"
                    >
                      <CheckCircle className="h-4 w-4 mr-2" />
                      Save Session
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => setShowForm(false)}
                      className="h-11 px-5 text-slate-300 hover:bg-white/[0.04] hover:text-white"
                    >
                      <X className="h-4 w-4 mr-2" />
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : events.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-64 text-center">
                  <div className="relative mb-4">
                    <div className="absolute inset-0 bg-indigo-500/20 blur-2xl rounded-full" />
                    <div className="relative h-14 w-14 rounded-2xl border border-dashed border-white/[0.15] bg-white/[0.02] backdrop-blur-xl flex items-center justify-center">
                      <CalendarIcon className="h-6 w-6 text-indigo-300" />
                    </div>
                  </div>
                  <p className="text-sm text-slate-400 max-w-sm">No upcoming study sessions yet.</p>
                  <Button
                    data-testid="empty-schedule-cta"
                    variant="link"
                    onClick={() => setShowForm(true)}
                    className="text-indigo-300 mt-1 hover:text-indigo-200"
                  >
                    Schedule one now →
                  </Button>
                </div>
              ) : (
                <div className="space-y-2.5">
                  {events.map((event, i) => (
                    <div
                      key={i}
                      className="group relative overflow-hidden flex items-center gap-4 p-4 rounded-xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-xl hover:border-indigo-400/30 hover:bg-white/[0.04] transition-all duration-300"
                    >
                      {/* Left rail */}
                      <div className="absolute left-0 top-0 h-full w-[3px] bg-gradient-to-b from-indigo-400 via-violet-500 to-indigo-600 shadow-[0_0_15px_rgba(99,102,241,0.5)] opacity-70 group-hover:opacity-100 transition-opacity" />

                      <div className="relative shrink-0 ml-1">
                        <div className="absolute inset-0 bg-indigo-500/20 blur-md rounded-lg" />
                        <div className="relative h-11 w-11 rounded-xl bg-gradient-to-br from-indigo-500/30 to-violet-500/30 border border-indigo-400/25 flex items-center justify-center backdrop-blur-xl">
                          <CalendarIcon className="h-5 w-5 text-indigo-200" />
                        </div>
                      </div>
                      <div className="flex-1 min-w-0">
                        <h4 className="font-semibold text-white text-sm md:text-base truncate group-hover:text-indigo-100 transition-colors">
                          {event.title}
                        </h4>
                        <p className="text-xs text-slate-400 flex items-center gap-1.5 mt-0.5">
                          <Clock className="h-3 w-3 text-slate-500" />
                          {format(new Date(event.start_time), "MMM d, h:mm a")}
                        </p>
                      </div>
                      <span className="text-[10px] font-mono text-slate-500 uppercase tracking-widest shrink-0 hidden sm:block">
                        Session
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}