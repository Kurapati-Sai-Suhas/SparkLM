import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  User,
  MessageSquare,
  Search,
  UserPlus,
  Check,
  X,
  MoreHorizontal,
  Sparkles,
  Users as UsersIcon,
} from "lucide-react";
import api, { userAPI } from "@/services/api";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu";

type TabKey = "friends" | "pending" | "find";

function AvatarBlock({ online }: { online?: boolean }) {
  return (
    <div className="relative shrink-0">
      <div className="h-10 w-10 rounded-full border border-indigo-400/25 bg-gradient-to-br from-indigo-500/20 to-indigo-700/10 backdrop-blur flex items-center justify-center shadow-[0_0_10px_rgba(99,102,241,0.2)]">
        <User className="h-5 w-5 text-indigo-200" />
      </div>
      {online !== undefined && (
        <span
          className={`absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full border-2 border-black ${
            online
              ? "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.9)]"
              : "bg-slate-600"
          }`}
        />
      )}
    </div>
  );
}

export default function Friends() {
  const [currentUser, setCurrentUser] = useState<any>(null);
  const [tab, setTab] = useState<TabKey>("friends");

  // Data State
  const [friends, setFriends] = useState<any[]>([]);
  const [pendingRequests, setPendingRequests] = useState<any[]>([]);
  const [searchResults, setSearchResults] = useState<any[]>([]);

  // Input State
  const [query, setQuery] = useState("");
  const [findQuery, setFindQuery] = useState("");
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState("");

  useEffect(() => {
    const loadData = async () => {
      try {
        // Independent — fetchFriends() doesn't use the profile result.
        const [profileRes] = await Promise.all([
          userAPI.getProfile(),
          fetchFriends(),
        ]);
        setCurrentUser(profileRes.data);
      } catch (err) {
        console.error("Failed to load friends data", err);
      }
    };
    loadData();
  }, []);

  const fetchFriends = async () => {
    try {
      const res = await api.get('/friends/');
      setPendingRequests(res.data.pending || []);
      setFriends(res.data.friends || []);
    } catch (err) {
      console.error("Error fetching friends", err);
    }
  };

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (findQuery.length < 3) return;

    setIsSearching(true);
    setSearchError("");
    setSearchResults([]);

    try {
      const res = await api.get(`/users/search/?q=${findQuery}`);
      if (res.data.users && res.data.users.length > 0) {
        setSearchResults(res.data.users);
      } else {
        setSearchError(`No users found matching "${findQuery}"`);
      }
    } catch (err: any) {
      setSearchError(err.response?.data?.error || "Backend error");
    } finally {
      setIsSearching(false);
    }
  };

  const sendRequest = async (userId: number) => {
    try {
      await api.post('/friends/request/', { receiver_id: userId });
      alert("Friend request sent! 🚀");
      setSearchResults(prev => prev.filter(u => u.id !== userId));
      fetchFriends();
    } catch (err: any) {
      alert(err.response?.data?.error || "Failed to send request.");
    }
  };

  const handleAction = async (connectionId: number, action: 'accept' | 'reject') => {
    try {
      await api.post(`/friends/request/${connectionId}/action/`, { action });
      fetchFriends();
    } catch (err) {
      alert(`Failed to ${action} request.`);
    }
  };

  const handleRemoveFriend = async (connectionId: number) => {
    try {
      await api.delete(`/friends/request/${connectionId}/`);
      fetchFriends();
    } catch (err) {
      alert("Failed to remove friend.");
    }
  };

  const filteredFriends = friends.filter((conn) => {
    const f = conn.sender.id === currentUser?.id ? conn.receiver : conn.sender;
    return f.username.toLowerCase().includes(query.toLowerCase());
  });

  const TABS: { key: TabKey; label: string; count?: number }[] = [
    { key: "friends", label: "My Friends", count: friends.length },
    { key: "pending", label: "Pending", count: pendingRequests.length },
    { key: "find", label: "Find Friends" },
  ];

  // Shared SparkLM tokens
  const glassPanel =
    "relative overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-2xl " +
    "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]";

  const glassInputWrap =
    "flex items-center gap-2 h-11 px-3 rounded-xl bg-white/[0.03] backdrop-blur border border-white/[0.08] " +
    "focus-within:border-indigo-400/50 focus-within:shadow-[0_0_18px_rgba(99,102,241,0.25)] transition-all";

  const primaryGlowBtn =
    "bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 " +
    "text-white border border-indigo-400/30 " +
    "shadow-[0_0_18px_rgba(99,102,241,0.5)] hover:shadow-[0_0_28px_rgba(99,102,241,0.7)] " +
    "transition-all font-medium";

  return (
    <div className="min-h-screen text-white font-sans">
      <div className="max-w-4xl mx-auto px-6 py-10 md:py-12">
        {/* HEADER */}
        <header className="mb-8">
          <div className="inline-flex items-center gap-2 rounded-full border border-indigo-400/25 bg-indigo-500/[0.08] backdrop-blur px-3 py-1.5 text-[11px] font-medium text-indigo-200 shadow-[0_0_15px_rgba(99,102,241,0.15)] mb-3">
            <UsersIcon className="h-3 w-3 text-indigo-300" />
            Network
          </div>
          <h1 className="text-3xl md:text-4xl font-semibold tracking-tight text-white">
            Your{" "}
            <span className="bg-gradient-to-r from-indigo-300 via-indigo-400 to-blue-400 bg-clip-text text-transparent">
              Network
            </span>
          </h1>
          <p className="text-sm md:text-base text-slate-400 mt-2">
            Manage your study connections and discover new peers.
          </p>
        </header>

        {/* TABS */}
        <div className="border-b border-white/[0.06] mb-6">
          <nav className="flex items-center gap-1" role="tablist">
            {TABS.map((t) => {
              const active = tab === t.key;
              return (
                <button
                  key={t.key}
                  role="tab"
                  aria-selected={active}
                  onClick={() => setTab(t.key)}
                  className={`relative px-4 py-3 text-sm font-medium transition-colors ${
                    active ? "text-white" : "text-slate-400 hover:text-white"
                  }`}
                >
                  <span className="inline-flex items-center gap-2">
                    {t.label}
                    {typeof t.count === "number" && (
                      <span
                        className={`inline-flex items-center justify-center h-5 min-w-[1.25rem] px-1.5 rounded-md text-[11px] font-mono font-semibold transition-all ${
                          active
                            ? "bg-indigo-500/15 text-indigo-300 border border-indigo-400/30 shadow-[0_0_10px_rgba(99,102,241,0.25)]"
                            : "bg-white/[0.03] text-slate-400 border border-white/[0.06]"
                        }`}
                      >
                        {t.count}
                      </span>
                    )}
                  </span>
                  {active && (
                    <span className="absolute left-0 right-0 -bottom-px h-[2px] bg-gradient-to-r from-indigo-400 to-indigo-500 shadow-[0_0_10px_rgba(99,102,241,0.7)]" />
                  )}
                </button>
              );
            })}
          </nav>
        </div>

        {/* PANEL: MY FRIENDS */}
        {tab === "friends" && (
          <div role="tabpanel">
            {/* Inline search */}
            <div className={`${glassInputWrap} mb-4`}>
              <Search className="h-4 w-4 text-slate-500 shrink-0" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter your friends"
                className="flex-1 bg-transparent text-sm text-white placeholder:text-slate-500 focus:outline-none"
              />
            </div>

            <div className={`${glassPanel} divide-y divide-white/[0.04]`}>
              {filteredFriends.length === 0 ? (
                <div className="px-4 py-14 text-center">
                  <div className="h-14 w-14 mx-auto rounded-2xl border border-dashed border-white/10 bg-white/[0.02] flex items-center justify-center mb-3">
                    <User className="h-6 w-6 text-slate-500" />
                  </div>
                  <p className="text-sm text-slate-400">No friends match that filter.</p>
                </div>
              ) : (
                filteredFriends.map((conn, idx) => {
                  const f = conn.sender.id === currentUser?.id ? conn.receiver : conn.sender;
                  return (
                    <div
                      key={conn.id}
                      className="flex items-center gap-3 px-4 py-3 hover:bg-white/[0.03] transition-colors animate-in fade-in slide-in-from-bottom-1 duration-300"
                      style={{ animationDelay: `${idx * 40}ms` }}
                    >
                      <Link to={`/chat`}>
                        <AvatarBlock online={true} />
                      </Link>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <Link
                            to={`/chat`}
                            className="text-sm font-medium text-white truncate hover:text-indigo-200 tracking-tight transition-colors"
                          >
                            {f.username}
                          </Link>
                        </div>
                        <p className="text-[11px] text-emerald-300 font-medium truncate mt-0.5 inline-flex items-center gap-1.5">
                          <span className="relative flex h-1.5 w-1.5">
                            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-70" />
                            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
                          </span>
                          Connected
                        </p>
                      </div>

                      <Link
                        to="/chat"
                        className={`inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-xs ${primaryGlowBtn}`}
                      >
                        <MessageSquare className="h-3.5 w-3.5" />
                        Message
                      </Link>

                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button
                            aria-label="More options"
                            className="h-8 w-8 rounded-lg flex items-center justify-center text-slate-400 hover:text-indigo-200 hover:bg-indigo-500/10 border border-transparent hover:border-indigo-400/30 transition-all"
                          >
                            <MoreHorizontal className="h-4 w-4" />
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent
                          align="end"
                          className="bg-[#0a0f1e]/95 backdrop-blur-2xl border border-white/[0.08] text-white shadow-[0_0_30px_rgba(99,102,241,0.25)]"
                        >
                          <DropdownMenuItem
                            className="text-rose-300 focus:text-rose-200 focus:bg-rose-500/10 cursor-pointer"
                            onClick={() => handleRemoveFriend(conn.id)}
                          >
                            Remove Friend
                          </DropdownMenuItem>
                          <DropdownMenuItem className="cursor-pointer text-slate-300 focus:text-white focus:bg-indigo-500/10">
                            Mute Notifications
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}

        {/* PANEL: PENDING */}
        {tab === "pending" && (
          <div role="tabpanel">
            {pendingRequests.length === 0 ? (
              <div className={`${glassPanel} px-4 py-14 text-center`}>
                <div className="h-14 w-14 mx-auto rounded-2xl border border-dashed border-white/10 bg-white/[0.02] flex items-center justify-center mb-3">
                  <UserPlus className="h-6 w-6 text-slate-500" />
                </div>
                <p className="text-sm text-slate-400">No pending requests.</p>
              </div>
            ) : (
              <div className={`${glassPanel} divide-y divide-white/[0.04]`}>
                {pendingRequests.map((conn, idx) => {
                  const isIncoming = conn.receiver.id === currentUser?.id;
                  const otherUser = isIncoming ? conn.sender : conn.receiver;
                  return (
                    <div
                      key={conn.id}
                      className="flex items-center gap-3 px-4 py-3 hover:bg-white/[0.03] transition-colors animate-in fade-in slide-in-from-bottom-1 duration-300"
                      style={{ animationDelay: `${idx * 40}ms` }}
                    >
                      <AvatarBlock />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="text-sm font-medium text-white truncate tracking-tight">
                            {otherUser.username}
                          </p>
                        </div>
                        <p className="text-[11px] text-slate-400 mt-0.5">
                          {isIncoming
                            ? "wants to connect"
                            : "Request sent · awaiting reply"}
                        </p>
                      </div>

                      {isIncoming ? (
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => handleAction(conn.id, "accept")}
                            className={`inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-xs ${primaryGlowBtn}`}
                          >
                            <Check className="h-3.5 w-3.5" />
                            Accept
                          </button>
                          <button
                            onClick={() => handleAction(conn.id, "reject")}
                            className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-xs font-medium text-slate-400 hover:text-white bg-white/[0.03] border border-white/[0.08] hover:bg-white/[0.06] hover:border-white/[0.12] transition-all"
                          >
                            <X className="h-3.5 w-3.5" />
                            Decline
                          </button>
                        </div>
                      ) : (
                        <button className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-xs font-medium text-slate-400 hover:text-white bg-white/[0.03] border border-white/[0.08] hover:bg-white/[0.06] hover:border-white/[0.12] transition-all">
                          Cancel
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* PANEL: FIND FRIENDS */}
        {tab === "find" && (
          <div role="tabpanel">
            <div className={`${glassPanel} p-6 mb-6`}>
              <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/70 to-transparent" />
              <div className="pointer-events-none absolute -top-20 -right-20 h-48 w-48 rounded-full bg-indigo-500/15 blur-3xl" />
              <div className="pointer-events-none absolute -bottom-20 -left-20 h-40 w-40 rounded-full bg-blue-500/10 blur-3xl" />

              <label className="relative block text-[10px] font-semibold uppercase tracking-[0.22em] text-slate-400 mb-3 inline-flex items-center gap-1.5">
                <Sparkles className="h-3 w-3 text-indigo-300" />
                Search by name or handle
              </label>
              <form onSubmit={handleSearch} className="relative flex items-center gap-2">
                <div className={`${glassInputWrap} flex-1`}>
                  <Search className="h-4 w-4 text-slate-500 shrink-0" />
                  <input
                    value={findQuery}
                    onChange={(e) => setFindQuery(e.target.value)}
                    placeholder="e.g. Suhas or Venkat"
                    className="flex-1 bg-transparent text-sm text-white placeholder:text-slate-500 focus:outline-none"
                  />
                </div>
                <button
                  type="submit"
                  disabled={isSearching}
                  className={`inline-flex items-center gap-1.5 h-11 px-5 rounded-xl text-sm ${primaryGlowBtn} disabled:opacity-50 disabled:shadow-none`}
                >
                  <UserPlus className="h-4 w-4" />
                  {isSearching ? "Searching…" : "Search"}
                </button>
              </form>

              <p className="relative mt-4 text-xs text-slate-500">
                Tip: You can search by full name, partial name, or username. Results will appear below once you search.
              </p>
            </div>

            {/* Results state */}
            {searchError ? (
              <div className="rounded-2xl border border-dashed border-rose-500/30 bg-rose-500/[0.05] backdrop-blur px-4 py-12 text-center">
                <p className="text-sm text-rose-300">{searchError}</p>
              </div>
            ) : searchResults.length > 0 ? (
              <div className={`${glassPanel} divide-y divide-white/[0.04]`}>
                {searchResults.map((user, idx) => (
                  <div
                    key={user.id}
                    className="flex items-center gap-3 px-4 py-3 hover:bg-white/[0.03] transition-colors animate-in fade-in slide-in-from-bottom-1 duration-300"
                    style={{ animationDelay: `${idx * 40}ms` }}
                  >
                    <AvatarBlock />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <p className="text-sm font-medium text-white truncate tracking-tight">
                          {user.first_name ? `${user.first_name} ${user.last_name}` : user.username}
                        </p>
                        <span className="text-[11px] font-mono text-slate-500">
                          @{user.username}
                        </span>
                      </div>
                    </div>

                    <button
                      onClick={() => sendRequest(user.id)}
                      className={`inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-xs ${primaryGlowBtn}`}
                    >
                      <UserPlus className="h-3.5 w-3.5" />
                      Add Friend
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <div className="mt-6 rounded-2xl border border-dashed border-white/10 bg-white/[0.02] backdrop-blur px-4 py-14 text-center">
                <div className="h-14 w-14 mx-auto rounded-2xl border border-white/[0.08] bg-white/[0.03] flex items-center justify-center mb-3">
                  <Search className="h-6 w-6 text-slate-500" />
                </div>
                <p className="text-sm text-slate-400">
                  {findQuery.trim()
                    ? `Press Search to find people matching "${findQuery.trim()}".`
                    : "Start typing above to discover new peers."}
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}