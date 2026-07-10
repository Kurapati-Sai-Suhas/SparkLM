import { useEffect, useState, useRef } from "react";
import { Send, WifiOff, Wifi, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";

export default function GroupChat({
  groupId,
  currentUser,
}: {
  groupId: string;
  currentUser: string;
}) {
  const [messages, setMessages] = useState<any[]>([]);
  const [newMessage, setNewMessage] = useState("");
  const [isConnected, setIsConnected] = useState(false);

  const ws = useRef<WebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const token = localStorage.getItem("authToken") || localStorage.getItem("access") || "";
    const wsUrl = `${import.meta.env.VITE_WS_URL || 'ws://127.0.0.1:8000'}/ws/chat/${groupId}/?token=${token}`;

    ws.current = new WebSocket(wsUrl);

    ws.current.onopen = () => {
      console.log("✅ WebSocket Connected!");
      setIsConnected(true);
    };

    ws.current.onclose = (event) => {
      console.log("❌ WebSocket Disconnected!", event.reason);
      setIsConnected(false);
    };

    ws.current.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === "history") {
        const loadedHistory = data.messages.map((msg: any) => ({
          message: msg.message || msg.content || "...",
          sender: msg.username || msg.sender || "System",
        }));
        setMessages(loadedHistory);
      } else if (data.type === "message" || !data.type) {
        const normalizedData = {
          message: data.message || data.text || "...",
          sender: data.sender || data.username || "System",
        };
        setMessages((prev) => [...prev, normalizedData]);
      } else if (data.type === "user_join" || data.type === "user_leave") {
        console.log(`${data.username} joined or left the chat.`);
      }
    };

    return () => {
      if (ws.current) {
        ws.current.close();
      }
    };
  }, [groupId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = (e: React.FormEvent) => {
    e.preventDefault();
    if (newMessage.trim() && ws.current && isConnected) {
      ws.current.send(
        JSON.stringify({
          message: newMessage.trim(),
          text: newMessage.trim(),
          sender: currentUser,
          username: currentUser,
        })
      );
      setNewMessage("");
    }
  };

  return (
    <div
      data-testid="group-chat"
      className="relative flex flex-col h-[520px] rounded-xl border border-border/60 bg-card/40 backdrop-blur-md shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)] overflow-hidden"
    >
      {/* top hairline accent */}
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/60 to-transparent" />

      {/* HEADER */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/60 bg-background/40 backdrop-blur">
        <div className="flex items-center gap-2.5">
          <div className="h-8 w-8 rounded-lg border border-border/60 bg-background/40 flex items-center justify-center shadow-[0_0_10px_rgba(59,130,246,0.25)]">
            <MessageSquare className="h-4 w-4 text-primary" />
          </div>
          <div>
            <h3 className="text-sm font-medium text-foreground leading-tight">Live Study Chat</h3>
            <p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              Realtime · WebSocket
            </p>
          </div>
        </div>

        <div
          data-testid="chat-connection-status"
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-mono tracking-wider backdrop-blur transition-all
            ${
              isConnected
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300 shadow-[0_0_10px_rgba(16,185,129,0.25)]"
                : "border-rose-500/30 bg-rose-500/10 text-rose-300 shadow-[0_0_10px_rgba(244,63,94,0.25)]"
            }`}
        >
          <span className="relative flex h-1.5 w-1.5">
            {isConnected && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-70" />
            )}
            <span
              className={`relative inline-flex h-1.5 w-1.5 rounded-full ${
                isConnected ? "bg-emerald-400" : "bg-rose-400"
              }`}
            />
          </span>
          {isConnected ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
          {isConnected ? "Connected" : "Disconnected"}
        </div>
      </div>

      {/* MESSAGES */}
      <div
        data-testid="chat-messages"
        className="flex-1 overflow-y-auto px-4 py-5 space-y-4"
      >
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center">
            <div className="h-14 w-14 rounded-2xl border border-dashed border-border/60 bg-background/30 backdrop-blur flex items-center justify-center mb-3">
              <MessageSquare className="h-6 w-6 text-muted-foreground" />
            </div>
            <p className="text-sm text-muted-foreground">No messages yet — say hello!</p>
          </div>
        ) : (
          messages.map((msg, idx) => {
            const safeSender = msg.sender || msg.username || "System";
            const isMe = safeSender === currentUser;

            return (
              <div
                key={idx}
                data-testid={`chat-msg-${idx}`}
                className={`flex gap-2.5 animate-in fade-in slide-in-from-bottom-1 duration-300 ${
                  isMe ? "flex-row-reverse" : "flex-row"
                }`}
              >
                <Avatar className="w-8 h-8 shrink-0 ring-1 ring-border/60">
                  <AvatarFallback
                    className={
                      isMe
                        ? "bg-primary/20 text-primary font-semibold text-xs"
                        : "bg-background/60 text-foreground font-semibold text-xs"
                    }
                  >
                    {safeSender.charAt(0).toUpperCase()}
                  </AvatarFallback>
                </Avatar>

                <div
                  className={`relative max-w-[75%] px-3.5 py-2.5 rounded-2xl text-sm border backdrop-blur transition-all
                    ${
                      isMe
                        ? "bg-primary text-primary-foreground rounded-tr-sm border-primary/60 shadow-[0_0_18px_rgba(59,130,246,0.35)]"
                        : "bg-card/60 text-foreground rounded-tl-sm border-border/60 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]"
                    }`}
                >
                  <p
                    className={`text-[10px] font-semibold uppercase tracking-[0.18em] mb-0.5 ${
                      isMe ? "text-primary-foreground/70" : "text-muted-foreground"
                    }`}
                  >
                    {safeSender}
                  </p>
                  <p className="leading-snug break-words">{msg.message}</p>
                </div>
              </div>
            );
          })
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* COMPOSER */}
      <form
        onSubmit={sendMessage}
        data-testid="chat-composer"
        className="p-3 border-t border-border/60 bg-background/40 backdrop-blur flex gap-2"
      >
        <div className="flex-1 relative">
          <Input
            data-testid="chat-input"
            value={newMessage}
            onChange={(e) => setNewMessage(e.target.value)}
            placeholder={isConnected ? "Type a message…" : "Reconnecting…"}
            disabled={!isConnected}
            className="h-10 bg-background/40 backdrop-blur border-border/60 text-foreground placeholder:text-muted-foreground/70 focus-visible:ring-1 focus-visible:ring-primary/60 focus-visible:border-primary/50 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.03)] transition-all disabled:opacity-60"
          />
        </div>
        <Button
          data-testid="chat-send-btn"
          type="submit"
          disabled={!isConnected || !newMessage.trim()}
          className="h-10 w-10 p-0 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 shadow-[0_0_15px_rgba(59,130,246,0.45)] hover:shadow-[0_0_25px_rgba(59,130,246,0.65)] transition-all disabled:opacity-50 disabled:shadow-none"
        >
          <Send className="w-4 h-4" />
        </Button>
      </form>
    </div>
  );
}