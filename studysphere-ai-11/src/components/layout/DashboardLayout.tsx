import { useState } from "react";
import { Menu } from "lucide-react";
import { AppSidebar } from "./AppSidebar";

export function DashboardLayout({ children }: { children: React.ReactNode }) {
  // Sidebar is a fixed w-64 column that never shrinks (flex-shrink-0):
  // on phone-width screens it left barely 100px for actual content,
  // clipping text mid-word. It's now an off-canvas drawer below the
  // md breakpoint, toggled from the hamburger button in the header;
  // above md it reverts to the original always-visible layout unchanged.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <div className="flex h-screen w-full font-sans overflow-hidden bg-gradient-to-br from-[#0a0f1e] via-[#08091a] to-[#050612]">
      {/* Ambient indigo glows */}
      <div className="pointer-events-none fixed -top-40 left-1/4 h-[500px] w-[500px] rounded-full bg-indigo-600/10 blur-[140px]" />
      <div className="pointer-events-none fixed bottom-0 right-1/4 h-[400px] w-[400px] rounded-full bg-blue-600/8 blur-[140px]" />

      {/* SIDEBAR */}
      <AppSidebar isOpen={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />

      {/* MAIN COLUMN */}
      <div className="relative flex-1 flex flex-col min-w-0 h-screen">
        {/* TOP HEADER */}
        <header
          data-testid="dashboard-header"
          className="relative h-16 flex items-center justify-between px-4 md:px-8 border-b border-white/[0.06] bg-white/[0.02] backdrop-blur-2xl shrink-0 z-30"
        >
          <div className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-indigo-400/40 to-transparent" />

          <div className="flex items-center gap-3">
            <button
              data-testid="mobile-nav-toggle"
              onClick={() => setMobileNavOpen(true)}
              aria-label="Open menu"
              className="md:hidden h-9 w-9 -ml-1 rounded-lg flex items-center justify-center text-slate-300 hover:text-white hover:bg-white/[0.06] transition-colors"
            >
              <Menu className="h-5 w-5" />
            </button>
            <div className="inline-flex items-center gap-2 rounded-full border border-indigo-400/20 bg-indigo-500/[0.08] backdrop-blur px-3 py-1.5 text-[11px] font-medium text-indigo-200 shadow-[0_0_20px_rgba(99,102,241,0.15)]">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-indigo-400" />
              </span>
              SparkLM · Live
            </div>
          </div>

          <div className="flex items-center gap-3">
            <span className="hidden md:inline-block text-[11px] font-medium uppercase tracking-[0.24em] text-slate-500">
              Adaptive AI Workspace
            </span>
            <div className="h-4 w-px bg-white/10" />
            <span className="text-[11px] font-medium uppercase tracking-[0.24em] text-indigo-300/80">
              Dark Mode
            </span>
          </div>
        </header>

        {/* PAGE CONTENT */}
        <div className="flex-1 overflow-y-auto [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
          {children}
        </div>
      </div>
    </div>
  );
}