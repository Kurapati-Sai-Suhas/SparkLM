import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Bell, CheckCheck, Trash2, Sparkles, Settings2 } from "lucide-react";

import { useState, useEffect } from "react";
import { userAPI } from "@/services/api";

export default function Notifications() {
  const [notifications, setNotifications] = useState<any[]>([]);

  // Was a raw fetch to the RELATIVE path "/api/notifications/". On Vercel
  // that resolved against the SPA's own origin, where the catch-all rewrite
  // in vercel.json serves index.html with HTTP 200 — so this parsed the
  // app's own HTML as JSON, threw, and was swallowed. The page has never
  // shown a real notification in production.
  //
  // The shared client supplies the origin (baseURL) plus the auth header,
  // the CSRF sentinel, and the 401 refresh-and-retry interceptor.
  useEffect(() => {
    userAPI.getNotifications()
      .then(({ data }) => {
        if (Array.isArray(data)) {
          setNotifications(data.map(n => ({
            ...n,
            icon: Bell,
            read: n.is_read
          })));
        }
      })
      .catch(console.error);
  }, []);

  const handleMarkAllRead = () => {
    userAPI.markAllNotificationsRead()
      .then(() => {
        setNotifications(notifications.map(n => ({ ...n, read: true })));
      })
      // A catch is REQUIRED, not defensive. `fetch` resolves on any HTTP
      // status, so a failed PUT still ran the .then and marked everything
      // read locally — the UI claimed a success the server never performed.
      // axios rejects on non-2xx, so the list now correctly stays unread,
      // and without this the rejection would be unhandled.
      .catch(console.error);
  };

  const unreadCount = notifications.filter(n => !n.read).length;

  // Local presentational renderer (pure JSX layout — no logic change)
  const renderNotification = (notification: any, forceRead: boolean = false) => {
    const isRead = forceRead || notification.read;
    return (
      <Card
        key={notification.id}
        className={`group relative overflow-hidden backdrop-blur-2xl transition-all duration-300 hover:border-white/[0.12] ${
          !isRead
            ? "bg-gradient-to-br from-indigo-500/[0.06] via-white/[0.02] to-transparent border-indigo-400/20 shadow-[0_0_25px_rgba(99,102,241,0.08)]"
            : "bg-white/[0.02] border-white/[0.06]"
        }`}
      >
        {/* Unread indicator rail */}
        {!isRead && (
          <div className="absolute left-0 top-0 h-full w-[3px] bg-gradient-to-b from-indigo-400 via-violet-500 to-indigo-600 shadow-[0_0_15px_rgba(99,102,241,0.5)]" />
        )}

        <CardContent className="p-5 pl-6">
          <div className="flex items-start gap-4">
            {/* Icon */}
            <div className="relative shrink-0">
              {!isRead && <div className="absolute inset-0 bg-indigo-500/30 blur-md rounded-full" />}
              <div className={`relative h-11 w-11 rounded-xl flex items-center justify-center backdrop-blur-xl ${
                !isRead
                  ? "bg-gradient-to-br from-indigo-500 to-violet-600 shadow-[0_0_20px_rgba(99,102,241,0.4)]"
                  : "bg-white/[0.03] border border-white/[0.08]"
              }`}>
                <notification.icon className={`h-5 w-5 ${!isRead ? "text-white" : "text-slate-400"}`} />
              </div>
            </div>

            {/* Content */}
            <div className="flex-1 min-w-0 space-y-1">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className={`font-semibold truncate ${!isRead ? "text-white" : "text-slate-300"}`}>
                    {notification.title}
                  </h3>
                  <p className="text-sm text-slate-400 mt-0.5 leading-relaxed">{notification.description}</p>
                </div>
                {!isRead && (
                  <div className="relative shrink-0 mt-1.5">
                    <span className="absolute inset-0 rounded-full bg-indigo-400 blur-sm opacity-60 animate-pulse" />
                    <span className="relative block h-2 w-2 rounded-full bg-indigo-400 shadow-[0_0_8px_rgba(99,102,241,0.9)]" />
                  </div>
                )}
              </div>
              <p className="text-[10px] text-slate-500 uppercase tracking-widest font-mono pt-1">
                {notification.time}
              </p>
            </div>

            {/* Actions */}
            <div className="flex gap-1 shrink-0">
              {!isRead && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 text-xs text-indigo-300 hover:text-indigo-200 hover:bg-indigo-500/10"
                >
                  Mark read
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-slate-500 hover:text-rose-300 hover:bg-rose-500/10"
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    );
  };

  const renderEmpty = (label: string) => (
    <div className="flex flex-col items-center justify-center text-center py-16 opacity-70">
      <div className="relative mb-4">
        <div className="absolute inset-0 bg-indigo-500/15 blur-2xl rounded-full" />
        <div className="relative h-14 w-14 rounded-2xl bg-white/[0.03] border border-white/[0.08] flex items-center justify-center backdrop-blur-xl">
          <Bell className="h-6 w-6 text-indigo-300" />
        </div>
      </div>
      <p className="text-sm text-slate-500 max-w-sm">{label}</p>
    </div>
  );

  return (
    <div className="relative min-h-screen bg-gradient-to-br from-[#0a0f1e] via-[#08091a] to-[#050612] -m-6 p-6 md:p-10">
      {/* Ambient glows */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-20 left-1/4 h-96 w-96 rounded-full bg-indigo-600/10 blur-[130px]" />
        <div className="absolute top-1/2 right-0 h-80 w-80 rounded-full bg-violet-500/10 blur-[130px]" />
        <div className="absolute bottom-0 left-0 h-72 w-72 rounded-full bg-blue-500/8 blur-[120px]" />
      </div>

      <div className="relative space-y-8 animate-in fade-in duration-500 max-w-5xl mx-auto">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-6 border-b border-white/[0.06]">
          <div>
            <div className="flex items-center gap-2 mb-3">
              <span className="inline-flex items-center gap-1.5 text-[10px] font-bold text-indigo-300 uppercase tracking-[0.25em] bg-indigo-500/10 border border-indigo-400/20 px-2.5 py-1 rounded-md">
                <Sparkles className="h-3 w-3" /> Activity Feed
              </span>
            </div>
            <h1 className="text-4xl md:text-5xl font-bold flex items-center gap-3 flex-wrap">
              <span className="bg-gradient-to-r from-white via-indigo-100 to-violet-200 bg-clip-text text-transparent">
                Notifications
              </span>
              {unreadCount > 0 && (
                <Badge className="relative bg-gradient-to-r from-indigo-500 to-violet-600 text-white border-0 shadow-[0_0_20px_rgba(99,102,241,0.5)] text-sm px-3 py-1 h-auto font-semibold">
                  <span className="absolute inset-0 rounded-full bg-white/20 blur-md opacity-50" />
                  <span className="relative">{unreadCount} new</span>
                </Badge>
              )}
            </h1>
            <p className="text-slate-400 mt-2 text-sm">Stay updated with your study activities in real-time.</p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={handleMarkAllRead}
              className="h-10 bg-white/[0.02] border-white/[0.08] text-slate-200 hover:bg-indigo-500/10 hover:border-indigo-400/30 hover:text-indigo-200 backdrop-blur-xl transition-all"
            >
              <CheckCheck className="h-4 w-4 mr-2" />
              Mark all as read
            </Button>
            <Button
              variant="outline"
              className="h-10 bg-white/[0.02] border-white/[0.08] text-slate-300 hover:bg-rose-500/10 hover:border-rose-400/30 hover:text-rose-200 backdrop-blur-xl transition-all"
            >
              <Trash2 className="h-4 w-4 mr-2" />
              Clear all
            </Button>
          </div>
        </div>

        {/* Notifications Tabs */}
        <Tabs defaultValue="all" className="w-full">
          <TabsList className="grid w-full max-w-md grid-cols-3 bg-white/[0.02] backdrop-blur-2xl border border-white/[0.06] p-1 h-11 rounded-xl">
            <TabsTrigger 
              value="all" 
              className="rounded-lg text-slate-400 data-[state=active]:bg-gradient-to-br data-[state=active]:from-indigo-500 data-[state=active]:to-violet-600 data-[state=active]:text-white data-[state=active]:shadow-[0_0_20px_rgba(99,102,241,0.35)] transition-all"
            >
              All
            </TabsTrigger>
            <TabsTrigger 
              value="unread" 
              className="rounded-lg text-slate-400 data-[state=active]:bg-gradient-to-br data-[state=active]:from-indigo-500 data-[state=active]:to-violet-600 data-[state=active]:text-white data-[state=active]:shadow-[0_0_20px_rgba(99,102,241,0.35)] transition-all"
            >
              Unread <span className="ml-1.5 text-[10px] font-mono opacity-80">({unreadCount})</span>
            </TabsTrigger>
            <TabsTrigger 
              value="read" 
              className="rounded-lg text-slate-400 data-[state=active]:bg-gradient-to-br data-[state=active]:from-indigo-500 data-[state=active]:to-violet-600 data-[state=active]:text-white data-[state=active]:shadow-[0_0_20px_rgba(99,102,241,0.35)] transition-all"
            >
              Read
            </TabsTrigger>
          </TabsList>

          <TabsContent value="all" className="space-y-3 mt-6 animate-in fade-in duration-300">
            {notifications.length > 0 
              ? notifications.map((n) => renderNotification(n))
              : renderEmpty("You're all caught up. New notifications will appear here as they arrive.")
            }
          </TabsContent>

          <TabsContent value="unread" className="space-y-3 mt-6 animate-in fade-in duration-300">
            {notifications.filter(n => !n.read).length > 0
              ? notifications.filter(n => !n.read).map((n) => renderNotification(n))
              : renderEmpty("No unread notifications. Nice work staying on top of things.")
            }
          </TabsContent>

          <TabsContent value="read" className="space-y-3 mt-6 animate-in fade-in duration-300">
            {notifications.filter(n => n.read).length > 0
              ? notifications.filter(n => n.read).map((n) => renderNotification(n, true))
              : renderEmpty("No read notifications yet.")
            }
          </TabsContent>
        </Tabs>

        {/* Notification Settings */}
        <Card className="relative overflow-hidden bg-white/[0.02] backdrop-blur-2xl border-white/[0.06] hover:border-white/[0.1] transition-all duration-300">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(139,92,246,0.08),transparent_60%)] pointer-events-none" />
          <div className="relative p-6 flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div className="flex items-start gap-4">
              <div className="h-11 w-11 rounded-xl bg-gradient-to-br from-indigo-500/20 to-violet-600/20 border border-indigo-400/20 flex items-center justify-center backdrop-blur-xl">
                <Settings2 className="h-5 w-5 text-indigo-300" />
              </div>
              <div>
                <h3 className="text-white font-semibold text-base">Notification Settings</h3>
                <p className="text-sm text-slate-400 mt-1 max-w-md">
                  Manage your notification preferences in Settings to control what updates you receive.
                </p>
              </div>
            </div>
            <Button
              variant="outline"
              className="h-10 bg-white/[0.02] border-white/[0.08] text-slate-200 hover:bg-indigo-500/10 hover:border-indigo-400/30 hover:text-indigo-200 backdrop-blur-xl transition-all shrink-0"
            >
              Go to Settings
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
