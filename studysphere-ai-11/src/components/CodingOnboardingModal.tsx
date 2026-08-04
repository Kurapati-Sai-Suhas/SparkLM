import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Brain, Zap, Loader2, Sparkles, CheckCircle2 } from "lucide-react";
import { getAccessToken } from "@/services/api";

interface ModalProps {
  groupId: string;
  groupTopic: string;
  onClose: () => void;
}

export default function CodingOnboardingModal({ groupId, groupTopic, onClose }: ModalProps) {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);

  const availableTopics = ["Array", "String", "Hash Table", "Two Pointers", "Sorting"];
  const [selectedTopics, setSelectedTopics] = useState<string[]>([]);

  const handleToggle = (topic: string) => {
    setSelectedTopics((prev) =>
      prev.includes(topic) ? prev.filter((t) => t !== topic) : [...prev, topic]
    );
  };

  const handleEnterPortal = async () => {
    setLoading(true);
    const token = getAccessToken();

    try {
      if (selectedTopics.length > 0) {
        await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/code/onboard/`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ known_topics: selectedTopics }),
        });
      }
      navigate(`/coding-portal?topic=${groupTopic}&group=${groupId}`);
    } catch (error) {
      console.error("Onboarding failed", error);
    } finally {
      setLoading(false);
      onClose();
    }
  };

  return (
    <Dialog open={true} onOpenChange={onClose}>
      <DialogContent
        data-testid="onboarding-modal"
        className="sm:max-w-md p-0 overflow-hidden bg-card/95 backdrop-blur-xl border-border/60 shadow-[0_0_60px_rgba(59,130,246,0.25)]"
      >
        {/* top hairline accent */}
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary to-transparent" />
        {/* radial glow */}
        <div className="pointer-events-none absolute -top-24 -right-24 h-56 w-56 rounded-full bg-primary/25 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-24 -left-24 h-56 w-56 rounded-full bg-indigo-500/20 blur-3xl" />

        <div className="relative p-6">
          <DialogHeader className="space-y-3">
            <div className="inline-flex items-center gap-1.5 self-start rounded-full border border-border/60 bg-background/40 backdrop-blur px-2.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.18em] text-primary">
              <Sparkles className="h-2.5 w-2.5" />
              GNN tensor calibration
            </div>

            <DialogTitle className="flex items-center gap-3 text-2xl font-semibold tracking-tight text-foreground">
              <span className="relative">
                <span className="absolute inset-0 bg-primary/30 blur-xl rounded-full" />
                <Brain className="relative text-primary h-7 w-7 drop-shadow-[0_0_10px_rgba(59,130,246,0.7)]" />
              </span>
              AI Calibration
            </DialogTitle>

            <DialogDescription className="text-sm text-muted-foreground leading-relaxed">
              Select the topics you've already mastered. Our PyTorch engine will skip these to find your optimal starting point.
            </DialogDescription>
          </DialogHeader>

          {/* progress meta */}
          <div className="mt-5 flex items-center justify-between text-[11px] font-mono uppercase tracking-[0.18em]">
            <span className="text-muted-foreground">Known topics</span>
            <span className="text-primary tabular-nums">
              {selectedTopics.length}
              <span className="text-muted-foreground"> / {availableTopics.length}</span>
            </span>
          </div>

          {/* Topic list */}
          <div data-testid="onboarding-topic-list" className="grid gap-2.5 py-5">
            {availableTopics.map((topic) => {
              const checked = selectedTopics.includes(topic);
              return (
                <label
                  key={topic}
                  htmlFor={topic}
                  data-testid={`onboarding-topic-${topic}`}
                  className={`group relative flex items-center gap-3 px-4 py-3 rounded-lg border cursor-pointer transition-all duration-200 backdrop-blur
                    ${
                      checked
                        ? "border-primary/50 bg-primary/10 shadow-[0_0_18px_rgba(59,130,246,0.25)]"
                        : "border-border/60 bg-background/30 hover:border-primary/40 hover:bg-background/50"
                    }`}
                >
                  <Checkbox
                    id={topic}
                    checked={checked}
                    onCheckedChange={() => handleToggle(topic)}
                    className="border-border data-[state=checked]:bg-primary data-[state=checked]:border-primary data-[state=checked]:shadow-[0_0_10px_rgba(59,130,246,0.6)] transition-all"
                  />
                  <span
                    className={`text-sm font-medium flex-1 transition-colors ${
                      checked ? "text-foreground" : "text-muted-foreground group-hover:text-foreground"
                    }`}
                  >
                    {topic}
                  </span>
                  {checked && (
                    <CheckCircle2 className="h-4 w-4 text-primary drop-shadow-[0_0_6px_rgba(59,130,246,0.7)]" />
                  )}
                </label>
              );
            })}
          </div>

          {/* divider */}
          <div className="h-px bg-gradient-to-r from-transparent via-border/60 to-transparent" />

          <DialogFooter className="mt-5 sm:justify-between items-center gap-2">
            <Button
              data-testid="onboarding-cancel-btn"
              variant="ghost"
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground hover:bg-muted/40"
            >
              Cancel
            </Button>
            <Button
              data-testid="onboarding-launch-btn"
              onClick={handleEnterPortal}
              disabled={loading}
              className="h-10 px-6 bg-primary text-primary-foreground hover:bg-primary/90 shadow-[0_0_18px_rgba(59,130,246,0.5)] hover:shadow-[0_0_28px_rgba(59,130,246,0.7)] transition-all font-medium disabled:opacity-60 disabled:shadow-none"
            >
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Calibrating…
                </>
              ) : (
                <>
                  <Zap className="w-4 h-4 mr-2" />
                  Launch Portal
                </>
              )}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}