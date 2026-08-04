import { useState, useEffect, useRef } from "react";
import { useParams } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Radio,
  RefreshCw,
  User,
  GitCommit,
  MessageSquare,
  Play,
  Terminal,
  Circle,
} from "lucide-react";
import * as Y from "yjs";
import { WebsocketProvider } from "y-websocket";
import { MonacoBinding } from "y-monaco";
import Editor from "@monaco-editor/react";
import { getAccessToken } from "@/services/api";

type Member = { id: string; name: string; role: string; color: string };

type Event = {
  id: string;
  type: "edit" | "join" | "message" | "run";
  who: string;
  text: string;
  time: string;
};

const MEMBERS: Member[] = [
  { id: "1", name: "Aarav Mehta",  role: "Editing line 24",  color: "bg-primary" },
  { id: "2", name: "Priya Sharma", role: "Viewing",           color: "bg-emerald-500" },
  { id: "3", name: "Daniel Park",  role: "Idle · 2m",         color: "bg-amber-500" },
  { id: "4", name: "Sara Khan",    role: "Editing line 41",   color: "bg-indigo-500" },
];

const EVENTS: Event[] = [
  { id: "e1", type: "edit",    who: "Aarav",  text: "edited consensus.py:24",          time: "Just now" },
  { id: "e2", type: "message", who: "Priya",  text: "Check the timeout in handleVote", time: "12s" },
  { id: "e3", type: "run",     who: "Sara",   text: "executed test suite — 14 passed", time: "48s" },
  { id: "e4", type: "join",    who: "Daniel", text: "joined the workspace",            time: "2m" },
  { id: "e5", type: "edit",    who: "Sara",   text: "edited raft.py:8",                time: "3m" },
];

const EVENT_META: Record<Event["type"], { icon: React.ComponentType<{ className?: string }>; color: string }> = {
  edit:    { icon: GitCommit,      color: "text-indigo-300"   },
  message: { icon: MessageSquare,  color: "text-blue-300"     },
  run:     { icon: Play,           color: "text-emerald-300"  },
  join:    { icon: User,           color: "text-amber-300"    },
};

const SAMPLE_CODE = `# consensus.py — Raft Leader Election
import asyncio
from typing import List

class RaftNode:
    def __init__(self, node_id: int, peers: List[int]):
        self.id = node_id
        self.peers = peers
        self.term = 0
        self.voted_for = None
        self.state = "follower"

    async def request_vote(self, candidate_id: int, term: int):
        if term > self.term:
            self.term = term
            self.voted_for = candidate_id
            return True
        return False

    async def start_election(self):
        self.term += 1
        self.state = "candidate"
        votes = 1
        for peer in self.peers:
            granted = await self.send_vote_request(peer)
            if granted:
                votes += 1
        if votes > len(self.peers) // 2:
            self.state = "leader"`;

export default function LiveCollaborativeWorkspace() {
  const { groupId } = useParams();
  const editorRef = useRef<any>(null);
  const providerRef = useRef<WebsocketProvider | null>(null);
  const ydocRef = useRef<Y.Doc | null>(null);
  const bindingRef = useRef<MonacoBinding | null>(null);

  useEffect(() => {
    const token = getAccessToken();
    if (!groupId || !token) return;

    const ydoc = new Y.Doc();
    ydocRef.current = ydoc;

    // Use standard WS connection, y-websocket appends the room name
    const provider = new WebsocketProvider(
      `${import.meta.env.VITE_WS_URL || 'ws://localhost:8000'}/ws`,
      `code/${groupId}/?token=${token}`,
      ydoc
    );
    providerRef.current = provider;

    return () => {
      bindingRef.current?.destroy();
      provider.disconnect();
      ydoc.destroy();
    };
  }, [groupId]);

  const handleEditorDidMount = (editor: any, monaco: any) => {
    editorRef.current = editor;
    const ydoc = ydocRef.current;
    const provider = providerRef.current;
    
    if (ydoc && provider) {
      const type = ydoc.getText("monaco");
      // If the document is empty, initialize it with sample code
      if (type.length === 0) {
        type.insert(0, SAMPLE_CODE);
      }
      bindingRef.current = new MonacoBinding(
        type,
        editor.getModel(),
        new Set([editor]),
        provider.awareness
      );
    }
  };

  // Shared glass card token
  const glassCard =
    "relative overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-2xl " +
    "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]";

  return (
    <div className="min-h-screen text-white p-6 md:p-8 font-sans">
      <div className="max-w-7xl mx-auto space-y-5">
        {/* ============ HEADER ============ */}
        <Card className={glassCard}>
          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/70 to-transparent" />
          <div className="pointer-events-none absolute -top-24 -right-24 h-56 w-56 rounded-full bg-indigo-500/25 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-24 -left-24 h-48 w-48 rounded-full bg-blue-500/15 blur-3xl" />

          <CardContent className="relative flex items-center justify-between gap-4 p-5 flex-wrap">
            <div className="flex items-center gap-3 min-w-0">
              <div className="relative">
                <div className="absolute inset-0 rounded-xl bg-indigo-500/30 blur-lg" />
                <div className="relative h-11 w-11 rounded-xl border border-indigo-400/30 bg-gradient-to-br from-indigo-500/25 to-indigo-700/15 backdrop-blur flex items-center justify-center shadow-[0_0_20px_rgba(99,102,241,0.45)]">
                  <Terminal className="h-5 w-5 text-indigo-200 drop-shadow-[0_0_6px_rgba(99,102,241,0.7)]" />
                </div>
              </div>
              <div className="min-w-0">
                <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-slate-400">
                  Study Group
                </p>
                <h1 className="text-lg font-semibold tracking-tight text-white flex items-center gap-2.5 mt-0.5">
                  Distributed Systems
                  <Badge
                    data-testid="live-badge"
                    className="inline-flex items-center gap-1.5 border border-rose-400/50 bg-rose-500/15 text-rose-200 text-[10px] font-mono tracking-[0.2em] uppercase px-2 py-0.5 shadow-[0_0_20px_rgba(244,63,94,0.55)] animate-pulse"
                  >
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-400 opacity-80" />
                      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-rose-400 shadow-[0_0_8px_rgba(244,63,94,1)]" />
                    </span>
                    Live
                  </Badge>
                </h1>
              </div>
            </div>

            <Button
              data-testid="sync-btn"
              className="relative h-10 px-5 rounded-xl bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 text-white border border-indigo-400/30 shadow-[0_0_20px_rgba(99,102,241,0.55)] hover:shadow-[0_0_30px_rgba(99,102,241,0.75)] transition-all font-medium"
            >
              <span className="absolute inset-0 rounded-xl bg-indigo-400/30 blur-md animate-pulse" />
              <span className="relative inline-flex items-center gap-2">
                <RefreshCw className="h-4 w-4" />
                Sync Code
              </span>
            </Button>
          </CardContent>
        </Card>

        {/* ============ SPLIT PANE ============ */}
        <div className="grid gap-5 lg:grid-cols-12">
          {/* EDITOR — high-end IDE frame */}
          <div className="lg:col-span-8 relative">
            {/* Aurora glow around the IDE */}
            <div className="pointer-events-none absolute -inset-1 rounded-3xl bg-gradient-to-br from-indigo-500/20 via-transparent to-blue-500/20 blur-2xl opacity-70" />

            <Card className={`relative ${glassCard} shadow-[0_20px_80px_-20px_rgba(99,102,241,0.45)]`}>
              {/* Top gradient hairline */}
              <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/60 to-transparent" />

              {/* IDE Toolbar */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06] bg-black/40 backdrop-blur-2xl">
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-full bg-rose-500/80 shadow-[0_0_6px_rgba(244,63,94,0.6)]" />
                    <span className="h-2.5 w-2.5 rounded-full bg-amber-500/80 shadow-[0_0_6px_rgba(245,158,11,0.6)]" />
                    <span className="h-2.5 w-2.5 rounded-full bg-emerald-500/80 shadow-[0_0_6px_rgba(16,185,129,0.6)]" />
                  </div>
                  <div className="h-4 w-px bg-white/10" />
                  <span className="text-xs font-mono text-slate-300 flex items-center gap-1.5">
                    <span className="h-1 w-1 rounded-full bg-indigo-400 animate-pulse" />
                    consensus.py
                  </span>
                </div>

                {/* Collaborator cursors */}
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-mono uppercase tracking-[0.18em] text-slate-500">
                    editing
                  </span>
                  <div className="flex -space-x-1.5">
                    {MEMBERS.slice(0, 3).map((m) => (
                      <div
                        key={m.id}
                        title={m.name}
                        className={`h-6 w-6 rounded-full ring-2 ring-black ${m.color} shadow-[0_0_12px_rgba(99,102,241,0.55)]`}
                      />
                    ))}
                  </div>
                </div>
              </div>

              {/* Code area with IDE frame */}
              <div className="relative">
                {/* Inner IDE glow */}
                <div className="pointer-events-none absolute inset-0 shadow-[inset_0_0_50px_rgba(99,102,241,0.1)]" />

                {/* Faint grid pattern */}
                <div
                  className="pointer-events-none absolute inset-0 opacity-[0.05]"
                  style={{
                    backgroundImage:
                      "linear-gradient(to right, white 1px, transparent 1px), linear-gradient(to bottom, white 1px, transparent 1px)",
                    backgroundSize: "20px 20px",
                  }}
                />

                {/* Fake live cursors overlay removed as Y-Monaco handles awareness natively */}
                <div className="h-[480px] w-full p-2">
                  <Editor
                    height="100%"
                    defaultLanguage="python"
                    theme="vs-dark"
                    options={{
                      minimap: { enabled: false },
                      padding: { top: 16 },
                      fontFamily: "var(--font-mono)",
                      fontSize: 13,
                      lineHeight: 24,
                      renderLineHighlight: "all",
                      scrollBeyondLastLine: false,
                    }}
                    onMount={handleEditorDidMount}
                  />
                </div>
              </div>

              {/* IDE Status strip */}
              <div className="flex items-center justify-between px-4 py-2.5 border-t border-white/[0.06] bg-black/40 backdrop-blur text-[10px] font-mono uppercase tracking-[0.2em] text-slate-500">
                <span className="inline-flex items-center gap-1.5">
                  <Radio className="h-3 w-3 text-emerald-400 animate-pulse drop-shadow-[0_0_4px_rgba(52,211,153,0.9)]" />
                  <span className="text-emerald-300">Synced</span>
                  <span className="text-slate-500">·</span>
                  <span>ws://sparklm</span>
                </span>
                <span>Python · UTF-8 · LF</span>
              </div>
            </Card>
          </div>

          {/* RIGHT PANE */}
          <div className="lg:col-span-4 space-y-5">
            {/* ACTIVE MEMBERS */}
            <Card className={glassCard}>
              <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-emerald-400/60 to-transparent" />
              <div className="border-b border-white/[0.06] px-4 py-3.5 flex items-center justify-between">
                <h3 className="text-sm font-medium text-white flex items-center gap-2 tracking-tight">
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-70" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,1)]" />
                  </span>
                  Active Members
                </h3>
                <Badge className="text-[10px] font-mono border border-emerald-400/30 bg-emerald-500/[0.08] text-emerald-300 shadow-[0_0_10px_rgba(16,185,129,0.2)]">
                  {MEMBERS.length} online
                </Badge>
              </div>
              <CardContent className="p-2 space-y-0.5">
                {MEMBERS.map((m) => (
                  <div
                    key={m.id}
                    className="group flex items-center gap-3 px-2.5 py-2 rounded-lg hover:bg-white/[0.03] hover:border-white/[0.06] border border-transparent transition-all"
                  >
                    <div className="relative">
                      <Avatar className="h-9 w-9 ring-1 ring-white/[0.08] group-hover:ring-indigo-400/30 transition-all">
                        <AvatarFallback className="bg-gradient-to-br from-indigo-500/25 to-indigo-700/15 text-indigo-200 text-xs font-semibold">
                          {m.name.charAt(0)}
                        </AvatarFallback>
                      </Avatar>
                      <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-emerald-400 ring-2 ring-black shadow-[0_0_8px_rgba(52,211,153,1)]" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-white truncate leading-tight tracking-tight">
                        {m.name}
                      </p>
                      <p className="text-[11px] text-slate-400 truncate mt-0.5">
                        {m.role}
                      </p>
                    </div>
                    <Circle className="h-2 w-2 fill-emerald-400 text-emerald-400 opacity-60 group-hover:opacity-100 drop-shadow-[0_0_4px_rgba(52,211,153,0.8)] transition-opacity" />
                  </div>
                ))}
              </CardContent>
            </Card>

            {/* EVENT LOG */}
            <Card className={glassCard}>
              <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/70 to-transparent" />
              <div className="border-b border-white/[0.06] px-4 py-3.5 flex items-center justify-between">
                <h3 className="text-sm font-medium text-white flex items-center gap-2 tracking-tight">
                  <Radio className="h-3.5 w-3.5 text-indigo-400 animate-pulse drop-shadow-[0_0_6px_rgba(99,102,241,0.8)]" />
                  Live Activity
                </h3>
                <span className="inline-flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-[0.2em] text-emerald-300">
                  <span className="h-1 w-1 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_6px_rgba(52,211,153,0.9)]" />
                  streaming
                </span>
              </div>
              <CardContent className="p-3 space-y-3 max-h-[300px] overflow-y-auto [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
                {EVENTS.map((ev, idx) => {
                  const meta = EVENT_META[ev.type];
                  const Icon = meta.icon;
                  return (
                    <div
                      key={ev.id}
                      className="flex items-start gap-2.5 animate-in fade-in slide-in-from-bottom-1 duration-300"
                      style={{ animationDelay: `${idx * 50}ms` }}
                    >
                      <div className={`h-7 w-7 rounded-lg border border-white/[0.08] bg-white/[0.03] backdrop-blur flex items-center justify-center shrink-0 ${meta.color} shadow-[0_0_10px_rgba(99,102,241,0.15)]`}>
                        <Icon className="h-3.5 w-3.5" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-[12px] leading-snug">
                          <span className="font-medium text-white">{ev.who}</span>{" "}
                          <span className="text-slate-400">{ev.text}</span>
                        </p>
                        <p className="text-[10px] font-mono text-slate-500 mt-0.5">
                          {ev.time}
                        </p>
                      </div>
                    </div>
                  );
                })}
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}
