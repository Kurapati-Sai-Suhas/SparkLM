import { useEffect, useState } from "react";
import {
  Home, Users, Calendar, MessageSquare, Layers, ListChecks,
  Bell, Settings, LogOut, FolderOpen, UserPlus, Terminal, X,
} from "lucide-react";
import { NavLink } from "react-router-dom";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { authAPI, userAPI } from "@/services/api";
import logo from "@/assets/logo.jpeg";

const menuItems = [
  { title: "Dashboard", url: "/", icon: Home },
  { title: "Study Groups", url: "/groups", icon: Users },
  { title: "Coding Hub", url: "/coding-hub", icon: Terminal },
  { title: "Friends", url: "/friends", icon: UserPlus },
  { title: "Flashcards", url: "/flashcards", icon: Layers },
  { title: "Quizzes", url: "/quiz", icon: ListChecks },
  { title: "Doubt Solver", url: "/doubt-solver", icon: MessageSquare },
  { title: "Schedule", url: "/schedule", icon: Calendar },
  { title: "File Library", url: "/files", icon: FolderOpen },
];

const bottomMenuItems = [
  { title: "Notifications", url: "/notifications", icon: Bell },
  { title: "Settings", url: "/settings", icon: Settings },
];

export function AppSidebar({
  isOpen = false,
  onClose,
}: {
  /** Mobile drawer state — ignored above the md breakpoint, where the
   *  sidebar is always visible (unchanged desktop behavior). */
  isOpen?: boolean;
  onClose?: () => void;
}) {
  const [user, setUser] = useState({ username: "Loading...", role: "Student" });

  useEffect(() => {
    const loadProfile = async () => {
      try {
        const response = await userAPI.getProfile();
        if (response.data && response.data.username) {
          setUser(response.data);
        }
      } catch (error) {
        setUser({ username: "Guest", role: "Student" });
      }
    };
    loadProfile();
  }, []);

  const handleLogout = () => {
    authAPI.logout();
  };

  const userInitials =
    user.username && user.username !== "Loading..."
      ? user.username.charAt(0).toUpperCase()
      : "?";

  const renderNavItem = (item: { title: string; url: string; icon: any }) => (
    <NavLink
      key={item.title}
      to={item.url}
      end={item.url === "/"}
      data-testid={`nav-${item.title.toLowerCase().replace(/\s+/g, "-")}`}
      onClick={onClose}
      className={({ isActive }) =>
        `group relative flex items-center gap-3 h-10 px-3 rounded-lg text-[13px] font-medium
         transition-all duration-200
         ${
           isActive
             ? "bg-gradient-to-r from-indigo-500/15 to-indigo-500/5 text-indigo-300 border border-indigo-400/30 shadow-[0_0_20px_rgba(99,102,241,0.2),inset_0_1px_0_rgba(255,255,255,0.05)]"
             : "text-slate-400 border border-transparent hover:text-white hover:bg-white/[0.04] hover:border-white/10"
         }`
      }
    >
      {({ isActive }) => (
        <>
          {isActive && (
            <span className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[3px] rounded-r-full bg-indigo-400 shadow-[0_0_10px_rgba(99,102,241,0.9)]" />
          )}
          <item.icon className={`h-[17px] w-[17px] shrink-0 transition-transform duration-200 group-hover:scale-110 ${isActive ? "drop-shadow-[0_0_6px_rgba(99,102,241,0.7)]" : ""}`} />
          <span className="truncate tracking-tight">{item.title}</span>
        </>
      )}
    </NavLink>
  );

  return (
    <>
      {/* Mobile-only backdrop: tapping outside the open drawer closes it. */}
      {isOpen && (
        <div
          data-testid="sidebar-backdrop"
          onClick={onClose}
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm md:hidden"
        />
      )}
      <aside
        data-testid="app-sidebar"
        className={`fixed inset-y-0 left-0 z-50 h-screen w-64 flex-shrink-0 flex flex-col font-sans overflow-hidden
          bg-gradient-to-b from-[#0a0f1e] via-[#08091a] to-[#050612]
          border-r border-white/[0.06]
          transition-transform duration-200 ease-out
          ${isOpen ? "translate-x-0" : "-translate-x-full"}
          md:static md:translate-x-0`}
      >
      {/* Ambient indigo glow */}
      <div className="pointer-events-none absolute -top-32 -left-20 h-72 w-72 rounded-full bg-indigo-600/20 blur-[100px]" />
      <div className="pointer-events-none absolute bottom-0 -right-20 h-64 w-64 rounded-full bg-blue-600/10 blur-[100px]" />

      {/* BRAND */}
      <div className="relative h-16 flex items-center px-5 border-b border-white/[0.06] shrink-0">
        <div className="flex items-center gap-3">
          <div className="relative">
            <img
              src={logo}
              alt="SparkLM"
              className="h-9 w-9 rounded-xl ring-1 ring-indigo-400/30 shadow-[0_0_20px_rgba(99,102,241,0.35)] object-cover"
            />
            <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-indigo-400 shadow-[0_0_8px_rgba(99,102,241,1)] animate-pulse" />
          </div>
          <div className="flex flex-col leading-none">
            <span className="text-[15px] font-semibold tracking-tight bg-gradient-to-r from-white to-slate-300 bg-clip-text text-transparent">
              SparkLM
            </span>
            <span className="text-[9px] font-medium uppercase tracking-[0.24em] text-indigo-300/70 mt-1">
              Adaptive AI
            </span>
          </div>
        </div>
        <button
          data-testid="sidebar-close-btn"
          onClick={onClose}
          aria-label="Close menu"
          className="md:hidden ml-auto h-8 w-8 rounded-lg flex items-center justify-center text-slate-400 hover:text-white hover:bg-white/[0.06] transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* NAV */}
      <nav className="relative flex-1 overflow-y-auto px-3 py-5 [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
        <p className="px-3 mb-2.5 text-[10px] font-semibold tracking-[0.24em] text-slate-500 uppercase">
          Workspace
        </p>
        <div className="space-y-1">{menuItems.map(renderNavItem)}</div>

        <p className="px-3 mt-7 mb-2.5 text-[10px] font-semibold tracking-[0.24em] text-slate-500 uppercase">
          Account
        </p>
        <div className="space-y-1">{bottomMenuItems.map(renderNavItem)}</div>
      </nav>

      {/* PROFILE FOOTER */}
      <div className="relative border-t border-white/[0.06] p-3 shrink-0">
        <div className="flex items-center gap-3 px-2.5 py-2.5 rounded-xl bg-white/[0.03] backdrop-blur-xl border border-white/[0.06] hover:border-indigo-400/20 hover:bg-white/[0.05] transition-all duration-200">
          <Avatar className="h-9 w-9 ring-1 ring-indigo-400/40 shadow-[0_0_15px_rgba(99,102,241,0.3)]">
            <AvatarFallback className="bg-gradient-to-br from-indigo-500 to-indigo-700 text-white font-semibold text-xs">
              {userInitials}
            </AvatarFallback>
          </Avatar>

          <div className="flex-1 min-w-0">
            <p
              data-testid="sidebar-username"
              className="text-[13px] font-medium text-white capitalize truncate leading-tight tracking-tight"
            >
              {user.username}
            </p>
            <p className="text-[10px] text-slate-400 capitalize truncate mt-0.5">
              {user.role || "Student"}
            </p>
          </div>

          <button
            data-testid="logout-btn"
            onClick={handleLogout}
            aria-label="Logout"
            className="group h-8 w-8 rounded-lg flex items-center justify-center border border-transparent
              text-slate-400 hover:text-rose-300 hover:bg-rose-500/10 hover:border-rose-500/20
              transition-all duration-200"
          >
            <LogOut className="h-4 w-4 group-hover:drop-shadow-[0_0_6px_rgba(244,63,94,0.8)] transition-all" />
          </button>
        </div>
      </div>
      </aside>
    </>
  );
}