import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { groupsAPI, userAPI } from "@/services/api";
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter,
} from "@/components/ui/dialog";
import {
  ArrowLeft, Users, FileText, MessageSquare, Upload, Download, Calendar, Brain,
  CheckCircle, XCircle, Trophy, RefreshCw, ClipboardList, Lock, LogIn,
  Cpu, Shield, Crown, ArrowRight, Sparkles, KeyRound,
} from "lucide-react";
import { Progress } from "@/components/ui/progress";
import "katex/dist/katex.min.css";
import Latex from "react-latex-next";
import GroupChat from "@/components/GroupChat";

export default function GroupDetail() {
  const params = useParams();
  const id = params.id || params.groupId;
  const navigate = useNavigate();

  const [group, setGroup] = useState<any>(null);
  const [files, setFiles] = useState<any[]>([]);
  const [currentUser, setCurrentUser] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const [membersList, setMembersList] = useState<any[]>([]);
  const [membersLoading, setMembersLoading] = useState(true);

  const [joinCode, setJoinCode] = useState("");

  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [fileToUpload, setFileToUpload] = useState<File | null>(null);
  const [fileTitle, setFileTitle] = useState("");

  const [assignedQuizzes, setAssignedQuizzes] = useState<any[]>([]);
  const [activeQuiz, setActiveQuiz] = useState<any>(null);
  const [currentQuestion, setCurrentQuestion] = useState(0);
  const [score, setScore] = useState(0);
  const [showResult, setShowResult] = useState(false);
  const [selectedOption, setSelectedOption] = useState<string | null>(null);
  const [isAnswerChecked, setIsAnswerChecked] = useState(false);

  const fetchFiles = useCallback(async () => {
    if (!id) return;
    try {
      const res = await groupsAPI.getMaterials(id);
      setFiles(res.data.results || res.data || []);
    } catch (e) { console.error("File fetch error", e); }
  }, [id]);

  const fetchAssignments = useCallback(async () => {
    if (!id) return;
    try {
      const res = await groupsAPI.getAssignedQuizzes(id);
      setAssignedQuizzes(res.data.results || res.data || []);
    } catch (error) { console.error("Failed to fetch group assignments", error); }
  }, [id]);

  const fetchMembers = useCallback(async () => {
    if (!id) return;
    setMembersLoading(true);
    try {
      const res = await groupsAPI.getMembers(id);
      setMembersList(res.data.results || res.data || []);
    } catch (error) {
      console.error("Failed to fetch members", error);
      setMembersList([]);
    } finally { setMembersLoading(false); }
  }, [id]);

  useEffect(() => {
    const fetchData = async () => {
      if (!id) return;
      try {
        // Independent calls — fire together instead of one round-trip
        // after the other.
        const [userRes, groupRes] = await Promise.all([
          userAPI.getProfile(),
          groupsAPI.getById(id),
        ]);
        setCurrentUser(userRes.data);
        setGroup(groupRes.data);
        fetchFiles();
        fetchAssignments();
        fetchMembers();
      } catch (error) {
        console.error("Failed to load group", error);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [id, fetchFiles, fetchAssignments, fetchMembers]);

  const handleUpload = async () => {
    if (!fileToUpload || !id || !fileTitle) { alert("Please select a file and enter a title!"); return; }
    setUploading(true);
    try {
      await groupsAPI.uploadMaterial(fileTitle, fileToUpload, id);
      alert("File Uploaded Successfully! 🎉");
      setFileToUpload(null); setFileTitle(""); setIsUploadOpen(false);
      fetchFiles();
    } catch (err) { console.error(err); alert("Upload failed. Check console."); }
    finally { setUploading(false); }
  };

  const handleJoin = async () => {
    try {
      await groupsAPI.join(joinCode);
      alert("Unlocked Successfully! Welcome to the group.");
      setJoinCode("");
      const groupRes = await groupsAPI.getById(id!);
      setGroup(groupRes.data);
      fetchMembers();
    } catch (err: any) { alert(err.response?.data?.error || "Invalid Access Code"); }
  };

  const handleStartQuiz = (quiz: any) => {
    setActiveQuiz(quiz); setCurrentQuestion(0); setScore(0);
    setShowResult(false); setIsAnswerChecked(false); setSelectedOption(null);
  };

  const handleCheckAnswer = () => {
    if (!selectedOption || !activeQuiz) return;
    const correct = activeQuiz.quiz_data[currentQuestion].correct_answer;
    if (selectedOption === correct) setScore(score + 1);
    setIsAnswerChecked(true);
  };

  const handleNext = () => {
    if (currentQuestion + 1 < activeQuiz.quiz_data.length) {
      setCurrentQuestion(currentQuestion + 1);
      setIsAnswerChecked(false); setSelectedOption(null);
    } else { setShowResult(true); }
  };

  // Quiz option styling — retuned to SparkLM indigo
  const getOptionStyle = (option: string) => {
    if (!isAnswerChecked || !activeQuiz) {
      return selectedOption === option
        ? "border-indigo-400/60 bg-indigo-500/10 ring-1 ring-indigo-400/60 shadow-[0_0_20px_rgba(99,102,241,0.4)] text-white"
        : "border-white/[0.08] bg-white/[0.02] backdrop-blur hover:border-indigo-400/40 hover:bg-white/[0.05] text-white";
    }
    const correct = activeQuiz.quiz_data[currentQuestion].correct_answer;
    if (option === correct)
      return "border-emerald-400/50 bg-emerald-500/10 text-emerald-200 shadow-[0_0_20px_rgba(16,185,129,0.4)]";
    if (option === selectedOption)
      return "border-rose-400/50 bg-rose-500/10 text-rose-200 shadow-[0_0_20px_rgba(244,63,94,0.4)]";
    return "border-white/[0.04] bg-white/[0.01] opacity-50 text-slate-500";
  };

  // SparkLM premium tokens
  const glassCard =
    "relative overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-2xl " +
    "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]";

  const sleekInput =
    "h-10 bg-white/[0.03] backdrop-blur border-white/[0.08] text-white " +
    "placeholder:text-slate-500 focus-visible:ring-1 focus-visible:ring-indigo-400/60 " +
    "focus-visible:border-indigo-400/50 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.03)] transition-all";

  const tabTrigger =
    "rounded-md text-sm font-medium text-slate-400 " +
    "data-[state=active]:text-white data-[state=active]:bg-white/[0.05] " +
    "data-[state=active]:shadow-[0_0_0_1px_rgba(255,255,255,0.06),0_0_18px_rgba(99,102,241,0.3)] transition-all duration-300";

  const primaryGlowBtn =
    "bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 " +
    "text-white border border-indigo-400/30 " +
    "shadow-[0_0_18px_rgba(99,102,241,0.5)] hover:shadow-[0_0_30px_rgba(99,102,241,0.75)] transition-all font-medium";

  if (loading)
    return (
      <div data-testid="group-detail-loading" className="flex h-[70vh] items-center justify-center">
        <div className="relative">
          <div className="h-14 w-14 rounded-full border-2 border-white/10" />
          <div className="absolute inset-0 h-14 w-14 rounded-full border-2 border-transparent border-t-indigo-400 animate-spin shadow-[0_0_30px_rgba(99,102,241,0.55)]" />
          <Users className="absolute inset-0 m-auto h-5 w-5 text-indigo-300" />
        </div>
      </div>
    );

  if (!group)
    return (
      <div className="p-10 text-center text-slate-500 font-mono text-sm">
        Group not found!
      </div>
    );

  const currentUserId = currentUser?.id;
  const creatorId = group?.creator?.id || group?.creator;
  const isMemberInList = group?.members?.some((m: any) => m.id === currentUserId || m === currentUserId);
  const isAllowed = Boolean(currentUserId) && (isMemberInList || currentUserId === creatorId);

  // ================= BOUNCER — Secure Portal =================
  if (!isAllowed) {
    return (
      <div className="relative flex flex-col items-center justify-center min-h-[85vh] p-8 font-sans animate-in zoom-in-95 fade-in duration-500">
        {/* Concentric radial glows for depth */}
        <div className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
          <div className="absolute top-20 left-1/2 -translate-x-1/2 h-[500px] w-[500px] rounded-full bg-indigo-500/20 blur-[130px]" />
          <div className="absolute bottom-10 left-1/2 -translate-x-1/2 h-[380px] w-[380px] rounded-full bg-blue-500/10 blur-[130px]" />
        </div>

        <Button
          data-testid="bouncer-back-btn"
          variant="ghost"
          className="mb-6 self-start text-slate-400 hover:text-white hover:bg-white/[0.04]"
          onClick={() => navigate("/groups")}
        >
          <ArrowLeft className="h-4 w-4 mr-2" /> Back to Directory
        </Button>

        <Card
          data-testid="bouncer-card"
          className="max-w-md w-full p-10 text-center relative overflow-hidden rounded-3xl border border-white/[0.08] bg-gradient-to-br from-white/[0.04] via-white/[0.02] to-transparent backdrop-blur-2xl shadow-[0_0_60px_-10px_rgba(99,102,241,0.4)]"
        >
          {/* Dual top hairlines */}
          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/80 to-transparent" />
          <div className="absolute inset-x-8 top-[3px] h-px bg-gradient-to-r from-transparent via-indigo-400/30 to-transparent" />

          {/* Radial glows */}
          <div className="absolute -top-24 -right-24 h-56 w-56 rounded-full bg-indigo-500/30 blur-3xl" />
          <div className="absolute -bottom-24 -left-24 h-56 w-56 rounded-full bg-blue-500/20 blur-3xl" />

          {/* Concentric lock icon */}
          <div className="relative mx-auto mb-6 w-24 h-24 flex items-center justify-center">
            {/* Outer pulsing ring */}
            <div className="absolute inset-0 rounded-full border border-indigo-400/30 animate-pulse" />
            {/* Mid ring */}
            <div className="absolute inset-2 rounded-full border border-indigo-400/40" />
            {/* Icon core */}
            <div className="relative h-16 w-16 rounded-2xl border border-indigo-400/40 bg-gradient-to-br from-indigo-500/25 to-indigo-700/15 backdrop-blur flex items-center justify-center shadow-[0_0_40px_rgba(99,102,241,0.6)]">
              <Lock className="w-7 h-7 text-indigo-200 drop-shadow-[0_0_10px_rgba(129,140,248,0.9)]" />
            </div>
          </div>

          <div className="inline-flex items-center gap-1.5 rounded-full border border-indigo-400/25 bg-indigo-500/[0.08] backdrop-blur px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.22em] text-indigo-200 mb-5 shadow-[0_0_15px_rgba(99,102,241,0.2)]">
            <Shield className="h-3 w-3" /> Restricted Access
          </div>

          <h2 className="text-3xl font-semibold tracking-tight text-white mb-3">
            Private Group
          </h2>
          <p className="text-sm text-slate-400 mb-8 leading-relaxed">
            You need to be a member of{" "}
            <strong className="text-indigo-300 font-semibold">{group.name}</strong> to view materials, assignments, and discussions.
          </p>

          {/* Access code input styled like a secure portal */}
          <div className="space-y-2">
            <div className="flex items-center gap-1.5 justify-center text-[9px] font-semibold uppercase tracking-[0.24em] text-slate-500">
              <KeyRound className="h-3 w-3 text-indigo-400" />
              Access Code
            </div>
            <div className="flex gap-2">
              <Input
                data-testid="bouncer-code-input"
                placeholder="Enter Access Code…"
                value={joinCode}
                onChange={(e) => setJoinCode(e.target.value)}
                className={`${sleekInput} h-12 font-mono tracking-[0.15em] text-center text-base`}
              />
              <Button
                data-testid="bouncer-unlock-btn"
                onClick={handleJoin}
                className={`h-12 px-5 rounded-lg ${primaryGlowBtn}`}
              >
                Unlock <LogIn className="w-4 h-4 ml-2" />
              </Button>
            </div>
          </div>

          <p className="mt-6 text-[10px] font-mono uppercase tracking-[0.22em] text-slate-500">
            Encrypted via SparkLM · v.secure
          </p>
        </Card>
      </div>
    );
  }

  // ================= MAIN =================
  return (
    <div
      data-testid="group-detail-page"
      className="relative space-y-6 p-6 md:p-10 max-w-7xl mx-auto font-sans animate-in fade-in duration-500"
    >
      {/* Header */}
      <div className="flex items-center gap-4 flex-wrap">
        <Button
          data-testid="back-btn"
          variant="outline"
          size="icon"
          onClick={() => navigate("/groups")}
          className="h-10 w-10 border-white/[0.08] bg-white/[0.03] backdrop-blur text-slate-300 hover:border-indigo-400/50 hover:text-indigo-200 hover:bg-indigo-500/10 transition-all"
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="flex-1 min-w-0">
          <h1 className="text-3xl md:text-4xl font-semibold tracking-tight text-white truncate">
            {group.name}
          </h1>
          <p className="text-sm text-slate-400 mt-1">{group.description}</p>
        </div>
        <div className="hidden md:inline-flex items-center gap-2 rounded-full border border-emerald-400/30 bg-emerald-500/[0.08] backdrop-blur px-3 py-1.5 text-xs font-mono tracking-wider text-emerald-300 shadow-[0_0_15px_rgba(16,185,129,0.2)]">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
          </span>
          <Shield className="w-3 h-3" /> {group.join_code || "N/A"}
        </div>
      </div>

      {/* Group Info */}
      <Card data-testid="group-info-card" className={glassCard}>
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/60 to-transparent" />
        <CardHeader className="border-b border-white/[0.06] pb-4">
          <CardTitle className="text-base font-medium text-white flex items-center gap-2 tracking-tight">
            <span className="h-1.5 w-1.5 rounded-full bg-indigo-400 shadow-[0_0_10px_rgba(99,102,241,1)]" />
            Group Information
          </CardTitle>
          <CardDescription className="text-sm text-slate-400">
            Created on {group.created_at ? new Date(group.created_at).toLocaleDateString() : "Unknown Date"}
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-5">
          <div className="inline-flex items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.03] backdrop-blur px-3 py-1.5 text-xs text-slate-400">
            <Users className="h-3.5 w-3.5 text-indigo-400" />
            <span className="tabular-nums text-white font-semibold">{membersList.length}</span>
            <span>/ {group.capacity} members</span>
          </div>
        </CardContent>
      </Card>

      {/* TABS */}
      <Tabs defaultValue="members" className="w-full">
        <TabsList
          data-testid="group-tabs"
          className="grid w-full grid-cols-4 h-11 p-1 rounded-lg bg-white/[0.03] backdrop-blur-md border border-white/[0.06]"
        >
          <TabsTrigger data-testid="tab-assignments" value="assignments" className={tabTrigger}>
            <ClipboardList className="h-4 w-4 mr-2" /><span className="hidden sm:inline">Assignments</span>
          </TabsTrigger>
          <TabsTrigger data-testid="tab-discussions" value="discussions" className={tabTrigger}>
            <MessageSquare className="h-4 w-4 mr-2" /><span className="hidden sm:inline">Discussions</span>
          </TabsTrigger>
          <TabsTrigger data-testid="tab-files" value="files" className={tabTrigger}>
            <FileText className="h-4 w-4 mr-2" /><span className="hidden sm:inline">Files</span>
          </TabsTrigger>
          <TabsTrigger data-testid="tab-members" value="members" className={tabTrigger}>
            <Users className="h-4 w-4 mr-2" /><span className="hidden sm:inline">Members</span>
          </TabsTrigger>
        </TabsList>

        {/* ASSIGNMENTS */}
        <TabsContent value="assignments" className="space-y-4 mt-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
          <Card className={glassCard}>
            <CardHeader className="border-b border-white/[0.06] pb-4">
              <CardTitle className="text-base font-medium text-white tracking-tight">Group Assignments</CardTitle>
              <CardDescription className="text-sm text-slate-400">
                Complete AI-generated quizzes assigned by your group leader.
              </CardDescription>
            </CardHeader>
            <CardContent className="pt-6">
              {assignedQuizzes.length === 0 ? (
                <div className="text-center py-14">
                  <div className="h-16 w-16 mx-auto rounded-2xl border border-dashed border-white/10 bg-white/[0.02] flex items-center justify-center mb-3">
                    <Calendar className="h-6 w-6 text-slate-500" />
                  </div>
                  <p className="text-sm text-slate-400">No pending assignments. You're all caught up!</p>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {assignedQuizzes.map((quiz, idx) => (
                    <Card
                      key={quiz.id}
                      data-testid={`quiz-card-${quiz.id}`}
                      className={`${glassCard} hover:border-indigo-400/40 hover:-translate-y-0.5 hover:shadow-[0_10px_40px_-10px_rgba(99,102,241,0.4)] transition-all duration-300 animate-in fade-in slide-in-from-bottom-2`}
                      style={{ animationDelay: `${idx * 50}ms` }}
                    >
                      <div className="absolute inset-y-0 left-0 w-px bg-gradient-to-b from-transparent via-amber-400/80 to-transparent" />
                      <CardContent className="p-5 flex flex-col justify-between h-full gap-4">
                        <div>
                          <h3 className="font-semibold text-white text-base mb-2 tracking-tight">{quiz.title}</h3>
                          <div className="inline-flex items-center gap-1.5 rounded-full border border-amber-400/30 bg-amber-500/10 backdrop-blur px-2.5 py-0.5 text-[11px] font-medium text-amber-300 mb-3 shadow-[0_0_10px_rgba(245,158,11,0.15)]">
                            <Calendar className="h-3 w-3" /> Due {new Date(quiz.deadline).toLocaleDateString()}
                          </div>
                          <p className="text-xs text-slate-400">
                            By: <span className="text-white font-medium">{quiz.creator_name}</span>
                          </p>
                        </div>
                        <Button
                          data-testid={`take-quiz-${quiz.id}`}
                          onClick={() => handleStartQuiz(quiz)}
                          className={`w-full h-9 ${primaryGlowBtn}`}
                        >
                          Take Quiz <ArrowRight className="ml-2 h-3.5 w-3.5" />
                        </Button>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* FILES */}
        <TabsContent value="files" className="space-y-4 mt-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
          <Card className={glassCard}>
            <CardHeader className="border-b border-white/[0.06] pb-4">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base font-medium text-white tracking-tight">Shared Files</CardTitle>
                <Dialog open={isUploadOpen} onOpenChange={setIsUploadOpen}>
                  <DialogTrigger asChild>
                    <Button data-testid="upload-trigger-btn" size="sm" className={`h-9 rounded-lg ${primaryGlowBtn}`}>
                      <Upload className="h-4 w-4 mr-2" /> Upload File
                    </Button>
                  </DialogTrigger>
                  <DialogContent className="bg-[#0a0f1e]/95 backdrop-blur-2xl border-white/[0.08] shadow-[0_0_60px_rgba(99,102,241,0.3)]">
                    <DialogHeader>
                      <DialogTitle className="text-white flex items-center gap-2 tracking-tight">
                        <Upload className="h-4 w-4 text-indigo-400" />
                        Upload Study Material
                      </DialogTitle>
                    </DialogHeader>
                    <div className="grid gap-4 py-4">
                      <div className="grid gap-1.5">
                        <Label className="text-[10px] uppercase tracking-[0.22em] text-slate-400 font-semibold">File Title</Label>
                        <Input
                          data-testid="upload-title-input"
                          placeholder="e.g. Calculus Notes"
                          value={fileTitle}
                          onChange={(e) => setFileTitle(e.target.value)}
                          className={sleekInput}
                        />
                      </div>
                      <div className="grid gap-1.5">
                        <Label className="text-[10px] uppercase tracking-[0.22em] text-slate-400 font-semibold">Select File</Label>
                        <Input
                          data-testid="upload-file-input"
                          type="file"
                          onChange={(e) => setFileToUpload(e.target.files ? e.target.files[0] : null)}
                          className={`${sleekInput} file:text-indigo-300 file:bg-transparent file:border-0 file:font-medium`}
                        />
                      </div>
                    </div>
                    <DialogFooter>
                      <Button
                        data-testid="upload-submit-btn"
                        onClick={handleUpload}
                        disabled={uploading}
                        className={`rounded-lg ${primaryGlowBtn}`}
                      >
                        {uploading ? "Uploading…" : "Upload"}
                      </Button>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>
              </div>
            </CardHeader>
            <CardContent className="pt-6">
              {files.length === 0 ? (
                <div className="text-center py-14">
                  <div className="h-16 w-16 mx-auto rounded-2xl border border-dashed border-white/10 bg-white/[0.02] flex items-center justify-center mb-3">
                    <FileText className="h-6 w-6 text-slate-500" />
                  </div>
                  <p className="text-sm text-slate-400">No files uploaded yet.</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {files.map((file: any) => (
                    <div
                      key={file.id}
                      data-testid={`file-row-${file.id}`}
                      className="group flex items-center justify-between gap-3 p-3 border border-white/[0.06] bg-white/[0.02] backdrop-blur rounded-xl hover:border-indigo-400/40 hover:bg-white/[0.04] hover:shadow-[0_0_20px_rgba(99,102,241,0.15)] transition-all"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="h-10 w-10 rounded-xl border border-white/[0.08] bg-white/[0.03] flex items-center justify-center shrink-0 group-hover:border-indigo-400/50 group-hover:bg-indigo-500/10 group-hover:shadow-[0_0_15px_rgba(99,102,241,0.4)] transition-all">
                          <FileText className="h-4 w-4 text-indigo-300" />
                        </div>
                        <div className="min-w-0">
                          <a
                            href={file.file}
                            target="_blank"
                            rel="noreferrer"
                            className="block font-medium text-sm text-white hover:text-indigo-200 truncate transition-colors"
                          >
                            {file.title || "Untitled"}
                          </a>
                          <p className="text-xs text-slate-500">{new Date(file.uploaded_at).toLocaleDateString()}</p>
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-slate-400 hover:text-indigo-200 hover:bg-indigo-500/10"
                        asChild
                      >
                        <a href={file.file} target="_blank" rel="noreferrer">
                          <Download className="h-4 w-4" />
                        </a>
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* DISCUSSIONS */}
        <TabsContent value="discussions" className="mt-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
          {id && currentUser ? (
            <div className={`${glassCard} p-1`}>
              <GroupChat groupId={id} currentUser={currentUser.username} />
            </div>
          ) : (
            <div className="text-center p-8 text-slate-500 font-mono text-sm">Loading chat…</div>
          )}
        </TabsContent>

        {/* MEMBERS */}
        <TabsContent value="members" className="space-y-4 mt-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
          <Card className={glassCard}>
            <CardHeader className="border-b border-white/[0.06] pb-4">
              <CardTitle className="text-base font-medium text-white tracking-tight">Group Members</CardTitle>
            </CardHeader>
            <CardContent className="pt-6">
              {membersLoading ? (
                <div className="text-center py-14">
                  <RefreshCw className="h-7 w-7 mx-auto mb-3 text-indigo-400 animate-spin drop-shadow-[0_0_8px_rgba(99,102,241,0.7)]" />
                  <p className="text-sm text-slate-400">Loading members…</p>
                </div>
              ) : membersList.length === 0 ? (
                <div className="text-center py-14">
                  <div className="h-16 w-16 mx-auto rounded-2xl border border-dashed border-white/10 bg-white/[0.02] flex items-center justify-center mb-3">
                    <Users className="h-6 w-6 text-slate-500" />
                  </div>
                  <p className="text-sm text-slate-400">No members have joined yet.</p>
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
                  {membersList.map((member: any, idx: number) => {
                    const isAdmin = group?.creator === member.id;
                    return (
                      <div
                        key={member.id}
                        data-testid={`member-${member.id}`}
                        className={`group flex items-center gap-3 p-4 rounded-2xl ${glassCard} hover:border-indigo-400/40 hover:-translate-y-0.5 hover:shadow-[0_10px_30px_-10px_rgba(99,102,241,0.4)] transition-all animate-in fade-in slide-in-from-bottom-2`}
                        style={{ animationDelay: `${idx * 40}ms` }}
                      >
                        {isAdmin && (
                          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-amber-400/80 to-transparent" />
                        )}
                        <Avatar className="h-11 w-11 ring-2 ring-white/[0.08] group-hover:ring-indigo-400/40 shadow-[0_0_12px_rgba(99,102,241,0.2)] transition-all">
                          <AvatarFallback className="bg-gradient-to-br from-indigo-500/30 to-indigo-700/20 text-indigo-200 font-semibold text-base">
                            {member.username ? member.username[0].toUpperCase() : "?"}
                          </AvatarFallback>
                        </Avatar>
                        <div className="flex flex-col min-w-0">
                          <span className="font-medium text-sm text-white truncate tracking-tight">{member.username}</span>
                          {isAdmin && (
                            <span className="mt-1 inline-flex items-center gap-1 w-fit rounded-full border border-amber-400/30 bg-amber-500/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.18em] text-amber-300 shadow-[0_0_10px_rgba(245,158,11,0.3)]">
                              <Crown className="h-2.5 w-2.5" /> Admin
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* ============ QUIZ MODAL PLAYER ============ */}
      <Dialog open={!!activeQuiz} onOpenChange={(open) => !open && setActiveQuiz(null)}>
        <DialogContent className="max-w-3xl h-[85vh] flex flex-col overflow-y-auto bg-[#0a0f1e]/95 backdrop-blur-2xl border-white/[0.08] shadow-[0_0_80px_rgba(99,102,241,0.35)]">
          {activeQuiz && (
            <>
              {!showResult ? (
                <div data-testid="quiz-player" className="space-y-6 animate-in fade-in py-2">
                  <div className="flex justify-between items-center gap-3 flex-wrap">
                    <h2 className="text-xl md:text-2xl font-semibold flex items-center gap-2 text-white tracking-tight">
                      <Brain className="text-indigo-400 h-5 w-5 drop-shadow-[0_0_6px_rgba(99,102,241,0.7)]" />
                      {activeQuiz.title}
                    </h2>
                    <Badge className="bg-indigo-500/10 text-indigo-300 border border-indigo-400/30 font-mono tracking-wider shadow-[0_0_12px_rgba(99,102,241,0.2)]">
                      Q {currentQuestion + 1} / {activeQuiz.quiz_data.length}
                    </Badge>
                  </div>

                  <Progress
                    value={(currentQuestion / activeQuiz.quiz_data.length) * 100}
                    className="h-1.5 bg-white/[0.04] [&>div]:bg-gradient-to-r [&>div]:from-indigo-500 [&>div]:to-indigo-400 [&>div]:shadow-[0_0_12px_rgba(99,102,241,0.7)]"
                  />

                  <Card className="shadow-none border-0 bg-transparent">
                    <CardHeader className="px-0">
                      <CardTitle className="text-lg md:text-xl leading-relaxed text-white font-medium">
                        <Latex>{activeQuiz.quiz_data[currentQuestion].question}</Latex>
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3 px-0">
                      {activeQuiz.quiz_data[currentQuestion].options.map((opt: string, i: number) => (
                        <div
                          key={i}
                          data-testid={`quiz-option-${i}`}
                          onClick={() => !isAnswerChecked && setSelectedOption(opt)}
                          className={`p-4 rounded-xl border cursor-pointer transition-all flex justify-between items-center ${getOptionStyle(opt)}`}
                        >
                          <span className="font-medium text-sm flex items-center gap-3">
                            <span className="h-7 w-7 rounded-md border border-white/[0.08] bg-white/[0.03] backdrop-blur flex items-center justify-center text-xs font-mono text-slate-400 shrink-0">
                              {String.fromCharCode(65 + i)}
                            </span>
                            <Latex>{opt}</Latex>
                          </span>
                          {isAnswerChecked && opt === activeQuiz.quiz_data[currentQuestion].correct_answer && (
                            <CheckCircle className="text-emerald-400 h-5 w-5 drop-shadow-[0_0_8px_rgba(52,211,153,0.9)]" />
                          )}
                          {isAnswerChecked && selectedOption === opt && opt !== activeQuiz.quiz_data[currentQuestion].correct_answer && (
                            <XCircle className="text-rose-400 h-5 w-5 drop-shadow-[0_0_8px_rgba(244,63,94,0.9)]" />
                          )}
                        </div>
                      ))}

                      <div className="pt-6 flex justify-end">
                        <Button
                          data-testid="quiz-action-btn"
                          onClick={isAnswerChecked ? handleNext : handleCheckAnswer}
                          disabled={!selectedOption}
                          className={`h-11 px-8 rounded-xl ${primaryGlowBtn} disabled:opacity-50 disabled:shadow-none`}
                        >
                          {isAnswerChecked
                            ? currentQuestion + 1 === activeQuiz.quiz_data.length ? "Finish Quiz" : "Next Question"
                            : "Check Answer"}
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                </div>
              ) : (
                <div
                  data-testid="quiz-result"
                  className="flex flex-col items-center justify-center h-full text-center space-y-6 animate-in zoom-in-95 fade-in duration-500"
                >
                  <div className="relative">
                    <div className="absolute inset-0 bg-amber-400/40 blur-3xl rounded-full animate-pulse" />
                    <div className="absolute inset-0 bg-indigo-500/20 blur-3xl rounded-full" />
                    <Trophy className="relative h-24 w-24 text-amber-400 drop-shadow-[0_0_30px_rgba(245,158,11,0.9)]" />
                  </div>
                  <h1 className="text-3xl md:text-4xl font-semibold tracking-tight text-white">
                    Assignment Completed!
                  </h1>
                  <p className="text-lg text-slate-400">
                    You scored{" "}
                    <span className="text-indigo-300 font-semibold text-3xl tabular-nums">{score}</span>
                    <span className="text-slate-500"> / {activeQuiz.quiz_data.length}</span>
                  </p>
                  <Button
                    onClick={() => setActiveQuiz(null)}
                    variant="outline"
                    className="h-11 rounded-lg border-white/[0.08] bg-white/[0.03] backdrop-blur text-slate-300 hover:border-indigo-400/50 hover:text-indigo-200 hover:bg-indigo-500/10 transition-all"
                  >
                    <RefreshCw className="h-4 w-4 mr-2" /> Close & Return to Group
                  </Button>
                </div>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}