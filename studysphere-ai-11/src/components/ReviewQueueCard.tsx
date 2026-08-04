import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { BookMarked, Clock3, Play } from "lucide-react";
import { getAccessToken } from "@/services/api";

interface ReviewItem {
  topic: string;
  retention_pct: number;
  accuracy_pct: number;
  effective_mastery_pct: number;
  days_since_practice: number;
  due: boolean;
}

/**
 * "Due for Review" (M7): the user's practiced topics ranked by predicted
 * recall from the HLR forgetting curve — the spaced-repetition payoff
 * surface. Renders nothing until there is at least one practiced topic.
 */
export default function ReviewQueueCard({
  onStartTopic,
}: {
  onStartTopic?: (topic: string) => void;
}) {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const token =
      getAccessToken();
    fetch(
      `${import.meta.env.VITE_API_URL || "http://localhost:8000"}/api/review/queue/`,
      { headers: { Authorization: `Bearer ${token}` } }
    )
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.queue) setItems(data.queue.slice(0, 4));
      })
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  if (!loaded || items.length === 0) return null;

  const dueCount = items.filter((i) => i.due).length;

  return (
    <Card className="relative overflow-hidden border-white/[0.06] bg-white/[0.02] backdrop-blur-2xl rounded-2xl">
      <CardHeader className="pb-3">
        <CardTitle className="text-[11px] font-medium tracking-[0.22em] uppercase text-indigo-300 flex items-center gap-2">
          <BookMarked className="h-3.5 w-3.5" />
          Due for Review
          {dueCount > 0 && (
            <Badge className="bg-amber-500/15 text-amber-300 border border-amber-400/30 text-[10px] px-2 py-0">
              {dueCount} due
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        {items.map((item) => (
          <div
            key={item.topic}
            className="flex items-center gap-3 rounded-lg border border-white/[0.06] bg-black/20 p-3"
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-sm font-medium text-white truncate">
                  {item.topic}
                </span>
                <span className="text-[11px] font-mono text-slate-400 flex items-center gap-1">
                  <Clock3 className="h-3 w-3" />
                  {item.days_since_practice}d ago
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Progress
                  value={item.retention_pct}
                  className={`h-1 flex-1 bg-white/[0.06] ${
                    item.due
                      ? "[&>div]:bg-amber-400"
                      : "[&>div]:bg-emerald-400"
                  }`}
                />
                <span
                  className={`text-[11px] font-mono tabular-nums ${
                    item.due ? "text-amber-300" : "text-emerald-300"
                  }`}
                >
                  {Math.round(item.retention_pct)}% recall
                </span>
              </div>
            </div>
            {onStartTopic && (
              <button
                onClick={() => onStartTopic(item.topic)}
                className="shrink-0 h-8 w-8 rounded-md border border-indigo-400/40 bg-indigo-500/[0.08] text-indigo-300 hover:bg-indigo-500/[0.2] transition-colors inline-flex items-center justify-center"
                title={`Review ${item.topic}`}
              >
                <Play className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
