import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Code2, Sparkles, ArrowRight, Terminal, Zap } from 'lucide-react';
import { getAccessToken } from "@/services/api";

interface Portal {
  id: number | string;
  name: string;
  description: string;
  is_active: boolean;
}

const CodingHub: React.FC = () => {
  const [portals, setPortals] = useState<Portal[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const navigate = useNavigate();

  useEffect(() => {
    const fetchPortals = async () => {
      try {
        const token = getAccessToken();

        const response = await fetch(`${import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'}/api/coding-portals/`, {
          method: 'GET',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
        });

        if (response.ok) {
          const data: Portal[] = await response.json();
          setPortals(data);
        } else {
          console.error('Failed to fetch portals. Status:', response.status);
        }
      } catch (error) {
        console.error('Network error:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchPortals();
  }, []);

  return (
    <div
      data-testid="coding-hub-page"
      className="relative min-h-screen text-white p-6 md:p-10 font-sans"
    >
      <div className="max-w-6xl mx-auto">
        {/* HERO */}
        <div
          data-testid="hub-hero"
          className="relative overflow-hidden rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.04] via-white/[0.02] to-transparent backdrop-blur-2xl p-8 md:p-12 mb-10 animate-in slide-in-from-bottom-4 fade-in duration-500"
        >
          {/* Masked grid */}
          <div
            className="absolute inset-0 opacity-[0.05] pointer-events-none"
            style={{
              backgroundImage:
                'linear-gradient(to right, rgba(255,255,255,1) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,1) 1px, transparent 1px)',
              backgroundSize: '40px 40px',
              maskImage: 'radial-gradient(ellipse at center, black 0%, transparent 75%)',
            }}
          />
          <div className="absolute -top-32 -right-24 h-80 w-80 rounded-full bg-indigo-500/25 blur-[100px]" />
          <div className="absolute -bottom-32 -left-24 h-72 w-72 rounded-full bg-blue-500/15 blur-[100px]" />

          <div className="relative max-w-2xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-indigo-400/25 bg-indigo-500/[0.08] backdrop-blur px-3 py-1.5 text-[11px] font-medium text-indigo-200 shadow-[0_0_20px_rgba(99,102,241,0.15)]">
              <Sparkles className="h-3 w-3 text-indigo-300" />
              GNN-powered learning paths
            </div>
            <h1 className="mt-5 text-4xl sm:text-5xl lg:text-6xl font-semibold tracking-tight text-white leading-[1.05]">
              Global{' '}
              <span className="bg-gradient-to-r from-indigo-300 via-indigo-400 to-blue-400 bg-clip-text text-transparent">
                Coding Hub
              </span>
            </h1>
            <p className="mt-4 text-base md:text-lg text-slate-400 leading-relaxed">
              Select a masterclass to begin your adaptive learning journey.
            </p>
          </div>
        </div>

        {loading ? (
          <div data-testid="hub-loading" className="flex justify-center items-center h-64">
            <div className="relative">
              <div className="h-14 w-14 rounded-full border-2 border-white/10" />
              <div className="absolute inset-0 h-14 w-14 rounded-full border-2 border-transparent border-t-indigo-400 animate-spin shadow-[0_0_30px_rgba(99,102,241,0.5)]" />
              <div className="absolute inset-0 flex items-center justify-center">
                <Code2 className="h-5 w-5 text-indigo-300" />
              </div>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {portals.map((portal, idx) => (
              <div
                key={portal.id}
                data-testid={`portal-card-${portal.id}`}
                onClick={() => navigate(`/coding-portal?portal_id=${portal.id}`)}
                className="group relative cursor-pointer animate-in fade-in slide-in-from-bottom-2 duration-500"
                style={{ animationDelay: `${idx * 60}ms` }}
              >
                {/* Outer aurora glow on hover */}
                <div className="absolute -inset-0.5 rounded-2xl bg-gradient-to-br from-indigo-500/0 via-indigo-500/0 to-blue-500/0 group-hover:from-indigo-500/40 group-hover:via-indigo-400/20 group-hover:to-blue-500/40 blur-lg opacity-0 group-hover:opacity-100 transition-opacity duration-500" />

                <div
                  className="relative h-full rounded-2xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-2xl p-6
                    shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]
                    transition-all duration-300
                    group-hover:border-indigo-400/40
                    group-hover:-translate-y-1
                    group-hover:shadow-[0_20px_60px_-15px_rgba(99,102,241,0.4)]"
                >
                  {/* Top hairline that lights up */}
                  <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/0 to-transparent group-hover:via-indigo-400/80 transition-all duration-500" />

                  <div className="flex justify-between items-start mb-5">
                    <div className="relative">
                      <div className="absolute inset-0 rounded-xl bg-indigo-500/30 blur-lg opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
                      <div className="relative h-11 w-11 rounded-xl border border-indigo-400/25 bg-indigo-500/10 backdrop-blur flex items-center justify-center text-indigo-300 group-hover:border-indigo-400/50 group-hover:text-indigo-200 group-hover:shadow-[0_0_20px_rgba(99,102,241,0.5)] transition-all">
                        <Code2 className="w-5 h-5" />
                      </div>
                    </div>

                    {portal.is_active && (
                      <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-400/25 bg-emerald-500/[0.08] backdrop-blur px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-emerald-300 shadow-[0_0_12px_rgba(16,185,129,0.2)]">
                        <span className="relative flex h-1.5 w-1.5">
                          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-70" />
                          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
                        </span>
                        Active
                      </span>
                    )}
                  </div>

                  <h2 className="text-lg font-semibold text-white group-hover:text-indigo-200 transition-colors mb-2 tracking-tight">
                    {portal.name}
                  </h2>
                  <p className="text-sm text-slate-400 line-clamp-3 leading-relaxed">
                    {portal.description ||
                      'Adaptive hierarchical problem set optimized by the PyTorch GNN engine.'}
                  </p>

                  <div className="mt-6 pt-4 border-t border-white/[0.06] flex items-center justify-between">
                    <span className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.20em] text-slate-500">
                      <Zap className="h-3 w-3 text-indigo-400" />
                      Adaptive
                    </span>
                    <div className="h-8 w-8 rounded-lg border border-white/[0.06] bg-white/[0.03] backdrop-blur text-slate-500 flex items-center justify-center opacity-0 -translate-x-2 group-hover:opacity-100 group-hover:translate-x-0 group-hover:border-indigo-400/50 group-hover:bg-indigo-500/10 group-hover:text-indigo-300 group-hover:shadow-[0_0_15px_rgba(99,102,241,0.5)] transition-all duration-300">
                      <ArrowRight className="w-4 h-4" />
                    </div>
                  </div>
                </div>
              </div>
            ))}

            {portals.length === 0 && (
              <div
                data-testid="hub-empty-state"
                className="col-span-full p-14 rounded-2xl border border-dashed border-white/10 bg-white/[0.02] backdrop-blur-2xl text-center"
              >
                <div className="h-16 w-16 mx-auto rounded-2xl border border-white/10 bg-white/[0.03] flex items-center justify-center mb-4">
                  <Terminal className="h-7 w-7 text-slate-500" />
                </div>
                <p className="text-sm text-slate-400">
                  No active portals found. Add some from the Django Admin.
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default CodingHub;