import { Suspense, lazy, useEffect, useState } from "react";
import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { GoogleOAuthProvider } from "@react-oauth/google";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { DashboardLayout } from "./components/layout/DashboardLayout";
import { ThemeProvider } from "@/components/theme-provider";
import { getAccessToken, bootstrapAuth } from "@/services/api";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined;

// Route-level code splitting: previously every page was a static import,
// so the Coding Portal's Monaco editor + y-monaco (collaborative editing)
// + recharts + framer-motion + katex all loaded before a user could even
// see the LOGIN page. Each of these becomes its own chunk, fetched only
// when its route is actually visited.
const Dashboard = lazy(() => import("./pages/Dashboard"));
const StudyGroups = lazy(() => import("./pages/StudyGroups"));
const GroupDetail = lazy(() => import("./pages/GroupDetail"));
const AIQuiz = lazy(() => import("./pages/AIQuiz"));
const QuizTaking = lazy(() => import("./pages/QuizTaking"));
const DoubtSolver = lazy(() => import("./pages/DoubtSolver"));
const CodingPortal = lazy(() => import("./components/CodingPortal"));
const AdaptiveCodingPortal = lazy(() => import("./pages/AdaptiveCodingPortal"));
const Schedule = lazy(() => import("./pages/Schedule"));
const FileLibrary = lazy(() => import("./pages/FileLibrary"));
const Notifications = lazy(() => import("./pages/Notifications"));
const Settings = lazy(() => import("./pages/Settings"));
const Auth = lazy(() => import("./pages/Auth"));
const NotFound = lazy(() => import("./pages/NotFound"));
const Friends = lazy(() => import("./pages/Friends"));
const CodingHub = lazy(() => import("./pages/CodingHub"));
const DirectChat = lazy(() => import("./pages/DirectChat"));
const LiveCollaborativeWorkspace = lazy(() => import("./pages/LiveCollaborativeWorkspace"));

const RouteFallback = () => (
  <div className="flex min-h-screen items-center justify-center bg-background">
    <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-400/30 border-t-indigo-400" />
  </div>
);

const queryClient = new QueryClient();

// Wraps children in GoogleOAuthProvider only when a Client ID is actually
// configured — so a deploy without VITE_GOOGLE_CLIENT_ID set (e.g. local
// dev before Google Cloud credentials exist) still renders the app
// normally; the Google button itself checks the same env var and simply
// doesn't render when it's absent, rather than crashing on a missing
// provider.
const GoogleAuthWrapper = ({ children }: { children: React.ReactNode }) =>
  GOOGLE_CLIENT_ID ? (
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>{children}</GoogleOAuthProvider>
  ) : (
    <>{children}</>
  );

const ProtectedRoute = ({ children }: { children: React.ReactNode }) => {
  // "Are we signed in?" is no longer answerable synchronously. The access
  // token lives in memory and is null on every fresh page load; the only
  // durable credential is an httpOnly cookie this code cannot read. So the
  // question goes to the server once, and the answer is awaited.
  //
  // Without this, removing the localStorage mirror would redirect every
  // reload to /auth despite a perfectly valid session.
  const [status, setStatus] = useState<"checking" | "in" | "out">(
    getAccessToken() ? "in" : "checking"
  );

  useEffect(() => {
    if (status !== "checking") return;
    let cancelled = false;
    bootstrapAuth().then((ok) => {
      if (!cancelled) setStatus(ok ? "in" : "out");
    });
    return () => {
      cancelled = true;
    };
  }, [status]);

  if (status === "checking") {
    return (
      <div
        data-testid="auth-checking"
        className="flex h-screen items-center justify-center bg-slate-950"
      >
        <div className="h-10 w-10 rounded-full border-2 border-transparent border-t-indigo-400 animate-spin" />
      </div>
    );
  }

  if (status === "out") {
    return <Navigate to="/auth" replace />;
  }

  return children;
};

const App = () => (
  <QueryClientProvider client={queryClient}>
    {/* App is permanently locked into dark mode — no toggle, no system theme */}
    <ThemeProvider>
      <TooltipProvider>
        <Toaster />
        <Sonner />
        <GoogleAuthWrapper>
        <BrowserRouter>
          <Suspense fallback={<RouteFallback />}>
            <Routes>
              <Route path="/auth" element={<Auth />} />
              <Route
                path="/*"
                element={
                  <ProtectedRoute>
                    <DashboardLayout>
                      <Suspense fallback={<RouteFallback />}>
                        <Routes>
                          <Route path="/" element={<Dashboard />} />
                          <Route path="/groups" element={<StudyGroups />} />
                          <Route path="/friends" element={<Friends />} />
                          <Route path="/groups/:groupId" element={<GroupDetail />} />
                          <Route path="/collab/:groupId" element={<LiveCollaborativeWorkspace />} />
                          <Route path="/quiz" element={<AIQuiz />} />
                          <Route path="/quiz/take/:quizId" element={<QuizTaking />} />
                          <Route path="/doubt-solver" element={<DoubtSolver />} />
                          <Route path="/coding-hub" element={<CodingHub />} />
                          <Route path="/code" element={<CodingPortal />} />
                          <Route path="/coding-portal" element={<AdaptiveCodingPortal />} />
                          <Route path="/schedule" element={<Schedule />} />
                          <Route path="/files" element={<FileLibrary />} />
                          <Route path="/notifications" element={<Notifications />} />
                          <Route path="/settings" element={<Settings />} />
                          <Route path="/chat" element={<DirectChat />} />
                          <Route path="*" element={<NotFound />} />
                        </Routes>
                      </Suspense>
                    </DashboardLayout>
                  </ProtectedRoute>
                }
              />
            </Routes>
          </Suspense>
        </BrowserRouter>
        </GoogleAuthWrapper>
      </TooltipProvider>
    </ThemeProvider>
  </QueryClientProvider>
);

export default App;