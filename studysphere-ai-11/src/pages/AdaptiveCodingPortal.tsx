import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import Editor from '@monaco-editor/react';
import {
  Brain,
  Play,
  CheckCircle,
  XCircle,
  Activity,
  Zap,
  ArrowLeft,
  Code2,
  Terminal,
  ChevronDown,
  Loader2,
  Target,
  AlertTriangle,
  Flame,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import 'katex/dist/katex.min.css';
import XaiComponentCharts from '../components/XaiComponentCharts';
import LearningPathVisualizer from '../components/LearningPathVisualizer';
import ReviewQueueCard from '../components/ReviewQueueCard';
import LanguageSelector from '../components/LanguageSelector';
import ProblemDescription from '../components/ProblemDescription';

// The language selector's value and the API's boilerplate_code key are not
// always the same string: the selector uses "js" while questions store their
// template under "javascript". Submissions were unaffected (the backend's
// LANGUAGE_IDS accepts both spellings), which is why the mismatch stayed
// invisible while every JavaScript user got an empty editor.
const BOILERPLATE_KEYS: Record<string, string[]> = {
  python: ['python'],
  java: ['java'],
  cpp: ['cpp'],
  c: ['c'],
  js: ['javascript', 'js'],
};

// Shown only when a problem genuinely has no template for the chosen
// language, so the editor is never blank and never left holding the previous
// language's code.
const EMPTY_STUB: Record<string, string> = {
  python: '# Write your solution here\n\n',
  java: '// Write your solution here\n\n',
  cpp: '// Write your solution here\n\n',
  c: '// Write your solution here\n\n',
  js: '// Write your solution here\n\n',
};

const FALLBACK_STUB = '// Write your solution here\n\n';

/** The stored starter template for a language, or null if there isn't one. */
function templateFor(boilerplate: any, lang: string): string | null {
  if (!boilerplate || typeof boilerplate !== 'object') return null;
  for (const key of BOILERPLATE_KEYS[lang] ?? [lang]) {
    const template = boilerplate[key];
    if (typeof template === 'string' && template.trim()) return template;
  }
  return null;
}

/** Languages this problem actually ships a usable template for. */
function availableLanguages(boilerplate: any): string[] {
  return Object.keys(BOILERPLATE_KEYS).filter(
    (lang) => templateFor(boilerplate, lang) !== null,
  );
}

export default function AdaptiveCodingPortal() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  const topic = searchParams.get('topic') || 'Array';
  const groupId = searchParams.get('group');

  const [problem, setProblem] = useState<any>(null);
  const [code, setCode] = useState('# Write your solution here\n\n');
  const [language, setLanguage] = useState('python');
  // True when this problem ships no template for the selected language, so
  // the UI can say so instead of silently presenting a blank file.
  const [templateMissing, setTemplateMissing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [results, setResults] = useState<any>(null);
  const [showLearningPath, setShowLearningPath] = useState(false);

  const fetchNextProblem = useCallback(async () => {
    setLoading(true);
    setResults(null);
    const token = localStorage.getItem('authToken') || localStorage.getItem('access');

    try {
      const response = await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/code/next/?topic=${topic}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await response.json();
      setProblem(data);

      const template = templateFor(data.boilerplate_code, language);
      setCode(template ?? EMPTY_STUB[language] ?? FALLBACK_STUB);
      setTemplateMissing(template === null);
    } catch (error) {
      console.error('Failed to fetch problem:', error);
    } finally {
      setLoading(false);
    }
  }, [topic, language]);

  useEffect(() => {
    fetchNextProblem();
  }, [fetchNextProblem]);

  useEffect(() => {
    if (!problem) return;
    // Always resolve the editor contents. The previous version only assigned
    // when a template existed, so switching to a language without one left
    // the PREVIOUS language's code in the editor - which could then be
    // submitted under the newly selected language.
    const template = templateFor(problem.boilerplate_code, language);
    setCode(template ?? EMPTY_STUB[language] ?? FALLBACK_STUB);
    setTemplateMissing(template === null);
  }, [language, problem]);

  const handleSubmit = async () => {
    setSubmitting(true);
    const token = localStorage.getItem('authToken') || localStorage.getItem('access');

    try {
      const response = await fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/code/submit/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          code: code,
          language: language,
          problem_id: problem.id,
        }),
      });
      const data = await response.json();
      setResults(data);
    } catch (error) {
      console.error('Failed to submit:', error);
    } finally {
      setSubmitting(false);
    }
  };

  // -------- Loading / empty states --------
  if (loading)
    return (
      <div
        data-testid="portal-loading"
        className="flex h-screen items-center justify-center bg-gradient-to-br from-[#0a0f1e] via-[#08091a] to-[#050612] text-white font-sans"
      >
        <div className="flex flex-col items-center gap-5">
          <div className="relative">
            <div className="h-16 w-16 rounded-full border-2 border-white/10" />
            <div className="absolute inset-0 h-16 w-16 rounded-full border-2 border-transparent border-t-indigo-400 animate-spin shadow-[0_0_35px_rgba(99,102,241,0.55)]" />
            <div className="absolute inset-0 flex items-center justify-center">
              <Brain className="h-5 w-5 text-indigo-300 drop-shadow-[0_0_8px_rgba(99,102,241,0.9)]" />
            </div>
          </div>
          <p className="text-sm font-mono text-slate-400 tracking-[0.22em] uppercase">
            Calibrating PyTorch tensor state…
          </p>
        </div>
      </div>
    );

  if (!problem)
    return (
      <div
        data-testid="portal-empty"
        className="flex h-screen items-center justify-center bg-gradient-to-br from-[#0a0f1e] to-[#050612] text-slate-500 font-mono text-sm"
      >
        No problems found.
      </div>
    );

  const difficultyClass =
    problem.difficulty === 'Easy'
      ? 'border-emerald-400/30 bg-emerald-500/10 text-emerald-300 shadow-[0_0_15px_rgba(16,185,129,0.25)]'
      : problem.difficulty === 'Medium'
      ? 'border-amber-400/30 bg-amber-500/10 text-amber-300 shadow-[0_0_15px_rgba(245,158,11,0.25)]'
      : 'border-rose-400/30 bg-rose-500/10 text-rose-300 shadow-[0_0_15px_rgba(244,63,94,0.25)]';

  const handleStartTopic = (newTopic: string) => {
    searchParams.set('topic', newTopic);
    setSearchParams(searchParams);
    setShowLearningPath(false);
  };

  if (showLearningPath) {
    return (
      <div className="relative z-50 min-h-screen bg-gradient-to-br from-[#0a0f1e] via-[#08091a] to-[#050612]">
        <div className="fixed bottom-8 right-8 z-[100]">
          <Button
            size="lg"
            className="rounded-full px-6 h-12 bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 text-white shadow-[0_0_25px_rgba(99,102,241,0.55)] hover:shadow-[0_0_40px_rgba(99,102,241,0.75)] border border-indigo-400/30 transition-all"
            onClick={() => setShowLearningPath(false)}
          >
            <ArrowLeft className="h-4 w-4 mr-2" /> Back to Editor
          </Button>
        </div>
        <LearningPathVisualizer onStartTopic={handleStartTopic} />
      </div>
    );
  }

  return (
    <div
      data-testid="coding-portal"
      className="relative flex h-screen text-white font-sans overflow-hidden bg-gradient-to-br from-[#0a0f1e] via-[#08091a] to-[#050612]"
    >
      {/* Ambient indigo/blue glow */}
      <div className="pointer-events-none absolute inset-0 -z-0 overflow-hidden">
        <div className="absolute -top-40 -left-40 h-[420px] w-[420px] rounded-full bg-indigo-500/15 blur-[140px]" />
        <div className="absolute top-1/2 right-0 h-[380px] w-[380px] rounded-full bg-blue-500/10 blur-[140px]" />
      </div>

      {/* =============== LEFT — Insights & Problem =============== */}
      <div
        data-testid="left-pane"
        className="relative z-10 w-1/2 h-full flex flex-col border-r border-white/[0.06] bg-white/[0.015] backdrop-blur-2xl overflow-y-auto"
      >
        {/* Top bar */}
        <div className="sticky top-0 z-20 flex items-center justify-between gap-3 border-b border-white/[0.06] bg-black/40 backdrop-blur-2xl px-5 py-3.5">
          <div className="flex items-center gap-3">
            <Button
              data-testid="exit-btn"
              variant="ghost"
              size="sm"
              className="h-8 text-slate-400 hover:text-white hover:bg-white/[0.04]"
              onClick={() => navigate(groupId ? `/groups/${groupId}` : '/coding-hub')}
            >
              <ArrowLeft className="h-4 w-4 mr-1.5" /> Exit
            </Button>
            <div className="h-5 w-px bg-white/10" />
            <h1 className="text-sm font-semibold text-white flex items-center gap-2 tracking-tight">
              <Activity className="h-4 w-4 text-indigo-400" />
              Adaptive Portal
            </h1>
          </div>
          <div className="flex items-center gap-3">
            <Button
              variant="outline"
              size="sm"
              className="border-indigo-400/40 text-indigo-300 bg-indigo-500/[0.08] hover:bg-indigo-500/[0.15] hover:text-indigo-200 hover:border-indigo-400/60 h-8 px-3 shadow-[0_0_15px_rgba(99,102,241,0.2)] hover:shadow-[0_0_20px_rgba(99,102,241,0.35)] transition-all"
              onClick={() => setShowLearningPath(true)}
            >
              <Target className="h-3.5 w-3.5 mr-1.5" />
              Learning Path
            </Button>
            <Badge
              data-testid="topic-badge"
              className="bg-indigo-500/10 text-indigo-300 border border-indigo-400/30 font-medium px-3 py-0.5 shadow-[0_0_12px_rgba(99,102,241,0.2)]"
            >
              {topic}
            </Badge>
          </div>
        </div>

        <div className="px-6 py-6 space-y-6">
          {/* DUE FOR REVIEW (M7 — HLR spaced repetition surface) */}
          <ReviewQueueCard onStartTopic={handleStartTopic} />

          {/* XAI INSIGHT PANEL */}
          <Card
            data-testid="xai-insight-panel"
            className="relative overflow-hidden border-white/[0.06] bg-white/[0.02] backdrop-blur-2xl rounded-2xl shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04),0_0_60px_rgba(99,102,241,0.1)]"
          >
            <div className="absolute -top-24 -right-24 h-52 w-52 rounded-full bg-indigo-500/30 blur-3xl" />
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/80 to-transparent" />

            <CardHeader className="pb-3 relative">
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.24em] text-indigo-300">
                  <span className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-60" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-indigo-400 shadow-[0_0_10px_rgba(129,140,248,1)]" />
                  </span>
                  <Brain className="h-3.5 w-3.5" />
                  Explainable AI · SHAP
                </CardTitle>
                <span className="font-mono text-[10px] text-slate-500 tracking-[0.18em]">
                  v.adv-xai
                </span>
              </div>
            </CardHeader>

            <CardContent className="relative">
              <p className="text-sm leading-relaxed text-slate-300 mb-5">
                {problem.explanation}
              </p>

              {problem.advanced_xai && problem.advanced_xai.xai && (
                <div data-testid="xai-metrics-grid" className="grid grid-cols-3 gap-2.5">
                  {/* Predicted */}
                  <div className="relative rounded-xl border border-white/[0.06] bg-white/[0.03] backdrop-blur p-3.5 overflow-hidden">
                    <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-emerald-400/70 to-transparent" />
                    <div className="flex items-center gap-1.5 text-[9px] uppercase tracking-[0.2em] text-slate-400 mb-2 font-semibold">
                      <Target className="h-3 w-3" />
                      Predicted
                    </div>
                    <div className="text-2xl font-semibold text-emerald-300 tabular-nums tracking-tight">
                      {problem.advanced_xai.xai.success_probability}
                      <span className="text-xs text-emerald-400/70 ml-0.5">%</span>
                    </div>
                  </div>

                  {/* Decay */}
                  <div className="relative rounded-xl border border-white/[0.06] bg-white/[0.03] backdrop-blur p-3.5 overflow-hidden">
                    <div
                      className={`absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent ${
                        problem.advanced_xai.decay_info.decay_percent > 40
                          ? 'via-rose-400/80'
                          : 'via-amber-400/80'
                      } to-transparent`}
                    />
                    <div className="flex items-center gap-1.5 text-[9px] uppercase tracking-[0.2em] text-slate-400 mb-2 font-semibold">
                      <AlertTriangle className="h-3 w-3" />
                      Decay
                    </div>
                    <div
                      className={`text-2xl font-semibold tabular-nums tracking-tight ${
                        problem.advanced_xai.decay_info.decay_percent > 40
                          ? 'text-rose-300'
                          : 'text-amber-300'
                      }`}
                    >
                      {problem.advanced_xai.decay_info.decay_percent}
                      <span className="text-xs opacity-70 ml-0.5">%</span>
                    </div>
                  </div>

                  {/* Dominant */}
                  <div className="relative rounded-xl border border-white/[0.06] bg-white/[0.03] backdrop-blur p-3.5 overflow-hidden">
                    <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/80 to-transparent" />
                    <div className="flex items-center gap-1.5 text-[9px] uppercase tracking-[0.2em] text-slate-400 mb-2 font-semibold">
                      <Flame className="h-3 w-3" />
                      Dominant
                    </div>
                    <div className="text-sm font-semibold text-indigo-300 capitalize truncate tracking-tight">
                      {problem.advanced_xai.xai.dominant_factor}
                    </div>
                  </div>
                </div>
              )}

              {problem.advanced_xai &&
                problem.advanced_xai.xai &&
                problem.advanced_xai.xai.recommendation && (
                  <div
                    data-testid="xai-recommendation"
                    className="mt-4 rounded-xl border border-white/[0.06] bg-white/[0.03] backdrop-blur p-3.5"
                  >
                    <div className="flex items-center gap-1.5 text-[9px] uppercase tracking-[0.2em] text-slate-400 mb-2 font-semibold">
                      <Flame className="h-3 w-3" />
                      Coach Recommendation
                    </div>
                    <p className="text-xs text-slate-300 leading-relaxed">
                      {problem.advanced_xai.xai.recommendation}
                    </p>
                  </div>
                )}

              {problem.advanced_xai &&
                problem.advanced_xai.xai &&
                problem.advanced_xai.xai.shap_values && (
                  <div
                    data-testid="xai-radar-container"
                    className="mt-6 pt-5 border-t border-white/[0.06] flex justify-center"
                  >
                    <XaiComponentCharts
                      radarData={problem.advanced_xai.xai.shap_values}
                      dominantFactor={problem.advanced_xai.xai.dominant_factor}
                    />
                  </div>
                )}
            </CardContent>
          </Card>

          {/* PROBLEM DESCRIPTION */}
          <div data-testid="problem-description-block">
            <div className="flex items-center gap-3 mb-4 flex-wrap">
              <h2 className="text-3xl font-semibold tracking-tight text-white">
                {problem.title}
              </h2>
              <span
                data-testid="difficulty-badge"
                className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.14em] backdrop-blur ${difficultyClass}`}
              >
                {problem.difficulty}
              </span>
            </div>

            <div className="relative rounded-2xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-2xl p-6 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]">
              <ProblemDescription
                content={problem.description}
                htmlClassName="prose prose-invert max-w-none text-sm leading-relaxed text-slate-300 prose-headings:text-white prose-strong:text-white prose-code:text-indigo-300 prose-code:bg-white/[0.05] prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:border prose-code:border-white/10 prose-code:before:content-none prose-code:after:content-none"
              />
            </div>
          </div>
        </div>
      </div>

      {/* =============== RIGHT — Editor & Console =============== */}
      <div
        data-testid="right-pane"
        className="relative z-10 w-1/2 h-full flex flex-col bg-[#0a0d14]"
      >
        {/* Editor toolbar */}
        <div
          data-testid="editor-toolbar"
          className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.06] bg-black/40 backdrop-blur-2xl"
        >
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-rose-500/70" />
              <span className="h-2.5 w-2.5 rounded-full bg-amber-500/70" />
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-500/70" />
            </div>
            <div className="h-4 w-px bg-white/10" />
            <div className="flex items-center gap-1.5 text-xs font-mono text-slate-400">
              <Code2 className="h-3.5 w-3.5 text-indigo-400" />
              <span>solution.{language === 'python' ? 'py' : language === 'java' ? 'java' : 'cpp'}</span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <LanguageSelector
              value={language}
              onChange={setLanguage}
              available={availableLanguages(problem?.boilerplate_code)}
            />

            {templateMissing && (
              <Badge
                variant="outline"
                data-testid="no-template-notice"
                className="border-amber-400/40 bg-amber-400/10 text-amber-300 text-[11px] font-normal"
              >
                No starter template for this language — writing from scratch
              </Badge>
            )}

            <Button
              data-testid="submit-code-btn"
              onClick={handleSubmit}
              disabled={submitting}
              className="h-9 px-4 text-xs font-medium rounded-lg bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 text-white border border-indigo-400/30 shadow-[0_0_18px_rgba(99,102,241,0.55)] hover:shadow-[0_0_30px_rgba(99,102,241,0.75)] transition-all disabled:opacity-60 disabled:shadow-none"
            >
              {submitting ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                  Running…
                </>
              ) : (
                <>
                  <Play className="h-3 w-3 mr-1.5 fill-current" />
                  Submit Code
                </>
              )}
            </Button>
          </div>
        </div>

        {/* Editor with immersive glow frame */}
        <div className="flex-1 relative">
          {/* Ambient glow around editor */}
          <div className="pointer-events-none absolute inset-0 shadow-[inset_0_0_40px_rgba(99,102,241,0.08)]" />
          <Editor
            height="100%"
            language={language}
            theme="vs-dark"
            value={code}
            onChange={(value) => setCode(value || '')}
            options={{
              minimap: { enabled: false },
              fontSize: 14,
              wordWrap: 'on',
              padding: { top: 16 },
            }}
          />
        </div>

        {/* TERMINAL CONSOLE */}
        <div
          data-testid="console-output"
          className="relative h-72 border-t border-white/[0.06] bg-[#050608] flex flex-col"
        >
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/[0.06] bg-black/50 backdrop-blur">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-400">
              <Terminal className="h-3.5 w-3.5 text-indigo-400" />
              console · stdout
            </div>
            {results && (
              <div
                className={`flex items-center gap-1.5 text-[11px] font-mono font-semibold ${
                  results.all_passed ? 'text-emerald-400' : 'text-rose-400'
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    results.all_passed
                      ? 'bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,1)]'
                      : 'bg-rose-400 shadow-[0_0_10px_rgba(244,63,94,1)]'
                  }`}
                />
                {results.all_passed ? 'exit 0' : 'exit 1'}
              </div>
            )}
          </div>

          <div className="flex-1 overflow-y-auto p-4 font-mono text-sm">
            {!results ? (
              <div className="flex items-center gap-2 text-slate-500">
                <span className="text-indigo-400">$</span>
                <span>awaiting submission</span>
                <span className="ml-0.5 inline-block h-3.5 w-1.5 bg-indigo-400/70 animate-pulse" />
              </div>
            ) : (
              <div
                data-testid="results-block"
                className="space-y-4 animate-in slide-in-from-bottom-4 fade-in duration-500"
              >
                <div className="flex items-center justify-between">
                  <h3
                    className={`text-base font-semibold flex items-center gap-2 tracking-tight ${
                      results.all_passed ? 'text-emerald-400' : 'text-rose-400'
                    }`}
                  >
                    {results.all_passed ? (
                      <CheckCircle className="h-4 w-4 drop-shadow-[0_0_6px_currentColor]" />
                    ) : (
                      <XCircle className="h-4 w-4 drop-shadow-[0_0_6px_currentColor]" />
                    )}
                    {results.status}
                  </h3>

                  <div
                    data-testid="test-summary"
                    className="inline-flex items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.03] backdrop-blur px-3 py-1 text-xs text-slate-400"
                  >
                    <span className="uppercase tracking-[0.2em] text-[10px] font-semibold">tests</span>
                    <span className="tabular-nums text-white font-semibold">
                      {results.passed}
                      <span className="text-slate-500"> / {results.total}</span>
                    </span>
                  </div>
                </div>

                <div className="h-1 w-full rounded-full bg-white/[0.04] overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-[width] duration-700 ${
                      results.all_passed
                        ? 'bg-gradient-to-r from-emerald-400 to-emerald-500 shadow-[0_0_12px_rgba(52,211,153,0.7)]'
                        : 'bg-gradient-to-r from-rose-400 to-rose-500 shadow-[0_0_12px_rgba(244,63,94,0.7)]'
                    }`}
                    style={{
                      width: `${results.total ? (results.passed / results.total) * 100 : 0}%`,
                    }}
                  />
                </div>

                {/* AGENTIC COACH — floating chat bubble */}
                {results.agentic_hint && (
                  <div className="mt-5 relative animate-in slide-in-from-bottom-2 fade-in duration-500">
                    <div className="flex items-start gap-3">
                      {/* Coach avatar */}
                      <div className="relative shrink-0">
                        <div className="absolute inset-0 rounded-full bg-indigo-500/40 blur-lg" />
                        <div className="relative h-9 w-9 rounded-full border border-indigo-400/40 bg-gradient-to-br from-indigo-500/30 to-indigo-700/20 backdrop-blur flex items-center justify-center shadow-[0_0_15px_rgba(99,102,241,0.4)]">
                          <Brain className="h-4 w-4 text-indigo-200 animate-pulse" />
                        </div>
                      </div>

                      {/* Chat bubble */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className="text-xs font-semibold text-indigo-300 tracking-tight">
                            Adaptive Coach
                          </span>
                          <span className="inline-flex items-center gap-1 rounded-full border border-indigo-400/25 bg-indigo-500/[0.08] px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.16em] text-indigo-300">
                            <span className="h-1 w-1 rounded-full bg-indigo-400 animate-pulse" />
                            AI
                          </span>
                        </div>

                        <div className="relative overflow-hidden rounded-2xl rounded-tl-md border border-indigo-400/25 bg-gradient-to-br from-indigo-500/[0.12] via-indigo-500/[0.06] to-transparent backdrop-blur-2xl p-4 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06),0_0_30px_rgba(99,102,241,0.15)]">
                          <div className="absolute -top-16 -right-16 h-32 w-32 rounded-full bg-indigo-500/25 blur-3xl" />
                          <p className="relative text-sm text-indigo-100/95 leading-relaxed font-sans whitespace-pre-wrap">
                            {results.agentic_hint}
                          </p>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {results.all_passed && (
                  <Button
                    data-testid="next-problem-btn"
                    onClick={fetchNextProblem}
                    className="w-full h-11 rounded-xl bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 text-white border border-indigo-400/30 shadow-[0_0_20px_rgba(99,102,241,0.55)] hover:shadow-[0_0_35px_rgba(99,102,241,0.75)] transition-all font-medium group"
                  >
                    <Zap className="h-4 w-4 mr-2" />
                    Load Next Problem
                    <span className="ml-2 opacity-60 group-hover:opacity-100 group-hover:translate-x-1 transition-all">→</span>
                  </Button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}