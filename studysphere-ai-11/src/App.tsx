import { Suspense, lazy } from "react";
import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { DashboardLayout } from "./components/layout/DashboardLayout";
import { ThemeProvider } from "@/components/theme-provider";

// Route-level code splitting: previously every page was a static import,
// so the Coding Portal's Monaco editor + y-monaco (collaborative editing)
// + recharts + framer-motion + katex all loaded before a user could even
// see the LOGIN page. Each of these becomes its own chunk, fetched only
// when its route is actually visited.
const Dashboard = lazy(() => import("./pages/Dashboard"));
const StudyGroups = lazy(() => import("./pages/StudyGroups"));
const GroupDetail = lazy(() => import("./pages/GroupDetail"));
const AIFlashcards = lazy(() => import("./pages/AIFlashcards"));
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

const ProtectedRoute = ({ children }: { children: React.ReactNode }) => {
  const token =
    localStorage.getItem("authToken") ||
    localStorage.getItem("token") ||
    localStorage.getItem("access_token") ||
    localStorage.getItem("access");

  if (!token) {
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
                          <Route path="/flashcards" element={<AIFlashcards />} />
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
      </TooltipProvider>
    </ThemeProvider>
  </QueryClientProvider>
);

export default App;