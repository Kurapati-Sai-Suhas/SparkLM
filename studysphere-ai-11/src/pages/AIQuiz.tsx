import { useState, useEffect } from "react";
import api, { groupsAPI, aiAPI, userAPI } from "@/services/api"; 
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Brain, Upload, Loader2, CheckCircle, XCircle, Trophy, RefreshCw, Users, Calendar, Sparkles, ChevronRight } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import 'katex/dist/katex.min.css';
import Latex from 'react-latex-next';

export default function AIQuiz() {
  const [currentUser, setCurrentUser] = useState<any>(null);
  const [groups, setGroups] = useState<any[]>([]);
  const [files, setFiles] = useState<any[]>([]);
  
  const [selectedGroup, setSelectedGroup] = useState<any>(null);
  const [selectedMaterialId, setSelectedMaterialId] = useState("");
  const [topic, setTopic] = useState("");
  const [isConfigMode, setIsConfigMode] = useState(false); 
  const [loading, setLoading] = useState(false);

  const [showEditMode, setShowEditMode] = useState(false);
  const [editableQuiz, setEditableQuiz] = useState<any[]>([]);
  const [deadline, setDeadline] = useState("");
  const [assignedQuizzes, setAssignedQuizzes] = useState<any[]>([]);

  const [quizData, setQuizData] = useState<any[]>([]);
  const [currentQuestion, setCurrentQuestion] = useState(0);
  const [score, setScore] = useState(0);
  const [showResult, setShowResult] = useState(false);
  const [selectedOption, setSelectedOption] = useState<string | null>(null);
  const [isAnswerChecked, setIsAnswerChecked] = useState(false);

  useEffect(() => {
    const loadInitialData = async () => {
        try {
            // Independent calls — fire together, not one after the other.
            const [userRes, groupsRes] = await Promise.all([
                userAPI.getProfile(),
                groupsAPI.getAll(),
            ]);
            setCurrentUser(userRes.data);
            setGroups(groupsRes.data.results || groupsRes.data || []);
        } catch (err) {
            console.error("Failed to load initial data", err);
        }
    };
    loadInitialData();
  }, []);

  const handleGroupChange = async (groupId: string) => {
    const groupObj = groups.find(g => String(g.id) === String(groupId));
    setSelectedGroup(groupObj);
    
    setFiles([]); 
    setAssignedQuizzes([]);
    
    try {
        const res = await groupsAPI.getMaterials(groupId);
        setFiles(res.data.results || res.data || []);

        const quizRes = await groupsAPI.getAssignedQuizzes(groupId);
        setAssignedQuizzes(quizRes.data.results || quizRes.data || []);
    } catch (error) {
        console.error("Failed to load group data", error);
    }
  };

  const handleGenerateQuiz = async (actionType: 'self' | 'assign') => {
    if (!selectedMaterialId) return alert("Please select a file!");
    
    setLoading(true);
    try {
      const res = await aiAPI.generateQuiz(selectedMaterialId, topic || "General", 5, "Medium");
      let generatedQuiz = res.data.quiz || res.data || [];
      
      // Handle case where LLM wrapped the array in an object (e.g. {"quiz": [...]})
      if (generatedQuiz && !Array.isArray(generatedQuiz)) {
          if (generatedQuiz.quiz && Array.isArray(generatedQuiz.quiz)) {
              generatedQuiz = generatedQuiz.quiz;
          } else if (generatedQuiz.questions && Array.isArray(generatedQuiz.questions)) {
              generatedQuiz = generatedQuiz.questions;
          } else {
              const firstArray = Object.values(generatedQuiz).find(v => Array.isArray(v));
              generatedQuiz = firstArray || [];
          }
      }
      
      if (!Array.isArray(generatedQuiz) || generatedQuiz.length === 0) {
        alert("AI could not generate questions. Try a different file.");
        setLoading(false);
        return;
      }

      if (actionType === 'assign') {
          setEditableQuiz(generatedQuiz);
          setShowEditMode(true);
          setIsConfigMode(false);
      } else {
          setQuizData(generatedQuiz);
          setCurrentQuestion(0);
          setScore(0);
          setShowResult(false);
          setIsAnswerChecked(false);
          setSelectedOption(null);
      }
    } catch (err) {
      console.error(err);
      alert("Failed to generate quiz. Check backend.");
    } finally {
      setLoading(false);
    }
  };

  const handleAssignQuizToGroup = async () => {
      if (!deadline) return alert("Please set a deadline for the students!");
      if (!selectedGroup) return;

      try {
          await api.post('/quizzes/assign/', {
              study_group: selectedGroup.id,
              topic: `${topic || "General"} Assignment`,
              quiz_data: editableQuiz,
              deadline: deadline
          });

          alert("✅ Quiz successfully assigned to the group!");
          setShowEditMode(false);
          setDeadline("");
          handleGroupChange(selectedGroup.id);
          
      } catch (error: any) {
          console.error("Assignment Error Payload:", error.response?.data);
          const errorData = error.response?.data;
          let errorMsg = "Failed to assign quiz.";
          
          if (errorData) {
              if (errorData.error) {
                  errorMsg = errorData.error;
              } else if (typeof errorData === 'object') {
                  const firstKey = Object.keys(errorData)[0];
                  errorMsg = `${firstKey.toUpperCase()}: ${errorData[firstKey]}`;
              }
          }
          
          alert(`Error: ${errorMsg}`);
      }
  };
  
  const handleStartAssignedQuiz = (quizItem: any) => {
      setQuizData(quizItem.quiz_data);
      setCurrentQuestion(0);
      setScore(0);
      setShowResult(false);
      setIsAnswerChecked(false);
      setSelectedOption(null);
  };

  const handleCheckAnswer = () => {
    if (!selectedOption) return;
    const correct = quizData[currentQuestion].correct_answer;
    if (selectedOption === correct) setScore(score + 1);
    setIsAnswerChecked(true);
  };

  const handleNext = () => {
    if (currentQuestion + 1 < quizData.length) {
      setCurrentQuestion(currentQuestion + 1);
      setIsAnswerChecked(false);
      setSelectedOption(null);
    } else {
      setShowResult(true);
    }
  };

  const getOptionStyle = (option: string) => {
    if (!isAnswerChecked) {
      return selectedOption === option 
        ? "border-indigo-400/60 bg-indigo-500/10 ring-1 ring-indigo-400/40 shadow-[0_0_20px_rgba(99,102,241,0.25)]" 
        : "border-white/[0.08] bg-white/[0.02] hover:bg-white/[0.04] hover:border-white/[0.15]";
    }
    const correct = quizData[currentQuestion].correct_answer;
    if (option === correct) return "border-emerald-400/60 bg-emerald-500/10 text-emerald-200 shadow-[0_0_25px_rgba(16,185,129,0.25)]";
    if (option === selectedOption) return "border-rose-400/60 bg-rose-500/10 text-rose-200 shadow-[0_0_20px_rgba(244,63,94,0.2)]";
    return "opacity-40 border-white/[0.06] bg-white/[0.01]";
  };

  // ---------- TEACHER MODE (REVIEW & ASSIGN) ----------
  if (showEditMode) {
      return (
          <div className="relative min-h-screen bg-gradient-to-br from-[#0a0f1e] via-[#08091a] to-[#050612] -m-6 p-6 md:p-10">
              {/* Ambient glow */}
              <div className="pointer-events-none absolute inset-0 overflow-hidden">
                  <div className="absolute -top-40 left-1/4 h-96 w-96 rounded-full bg-indigo-600/10 blur-[120px]" />
                  <div className="absolute top-1/3 right-0 h-80 w-80 rounded-full bg-blue-500/10 blur-[120px]" />
              </div>

              <div className="relative max-w-4xl mx-auto space-y-8 animate-in fade-in duration-500">
                  {/* Header */}
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-6 border-b border-white/[0.06]">
                      <div>
                          <div className="flex items-center gap-3 mb-2">
                              <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center shadow-[0_0_25px_rgba(59,130,246,0.35)]">
                                  <Users className="h-5 w-5 text-white"/>
                              </div>
                              <h2 className="text-3xl font-bold bg-gradient-to-r from-white via-blue-100 to-indigo-200 bg-clip-text text-transparent">
                                  Review &amp; Assign Quiz
                              </h2>
                          </div>
                          <p className="text-slate-400 text-sm max-w-xl">
                              Fine-tune the AI-generated questions, set a deadline, and deploy this quiz to your study group.
                          </p>
                      </div>
                      <Badge className="self-start bg-blue-500/10 text-blue-300 border border-blue-400/20 backdrop-blur-xl px-3 py-1.5 text-xs uppercase tracking-wider">
                          <Sparkles className="h-3 w-3 mr-1.5"/> Teacher Mode
                      </Badge>
                  </div>

                  {/* Editable questions */}
                  <div className="space-y-5">
                      {editableQuiz.map((q, index) => (
                          <Card key={index} className="group relative overflow-hidden bg-white/[0.02] backdrop-blur-2xl border-white/[0.06] hover:border-white/[0.1] transition-all duration-300">
                              <div className="absolute left-0 top-0 h-full w-[3px] bg-gradient-to-b from-indigo-400 via-blue-500 to-indigo-600 shadow-[0_0_15px_rgba(99,102,241,0.4)]"/>
                              <CardContent className="p-6 space-y-4 pl-7">
                                  <div className="flex items-center gap-3">
                                      <span className="text-[10px] font-bold text-indigo-300 uppercase tracking-[0.2em] bg-indigo-500/10 border border-indigo-400/20 px-2.5 py-1 rounded-md">
                                          Question {index + 1}
                                      </span>
                                  </div>
                                  <textarea 
                                      className="w-full p-4 bg-white/[0.02] border border-white/[0.08] rounded-lg text-white placeholder:text-slate-500 focus:ring-2 focus:ring-indigo-400/40 focus:border-indigo-400/40 outline-none resize-none transition-all"
                                      rows={2}
                                      value={q.question}
                                      onChange={(e) => {
                                          const updatedQuiz = [...editableQuiz];
                                          updatedQuiz[index].question = e.target.value;
                                          setEditableQuiz(updatedQuiz);
                                      }}
                                  />
                                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                      {q.options.map((opt: string, i: number) => (
                                          <div 
                                              key={i} 
                                              className={`p-3 rounded-lg text-sm flex items-center gap-2 border transition-all ${
                                                  opt === q.correct_answer 
                                                      ? 'bg-emerald-500/10 border-emerald-400/30 text-emerald-200 shadow-[0_0_20px_rgba(16,185,129,0.15)]' 
                                                      : 'bg-white/[0.02] border-white/[0.06] text-slate-400'
                                              }`}
                                          >
                                              {opt === q.correct_answer && <CheckCircle className="h-4 w-4 text-emerald-400 shrink-0"/>}
                                              <span className="truncate">{opt}</span>
                                          </div>
                                      ))}
                                  </div>
                              </CardContent>
                          </Card>
                      ))}
                  </div>

                  {/* Deadline & CTA */}
                  <Card className="relative overflow-hidden bg-gradient-to-br from-indigo-500/[0.08] via-blue-500/[0.04] to-transparent backdrop-blur-2xl border-indigo-400/20">
                      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(99,102,241,0.15),transparent_60%)] pointer-events-none"/>
                      <CardContent className="relative p-6 md:p-8 flex flex-col md:flex-row gap-5 items-end">
                          <div className="flex-1 w-full space-y-2.5">
                              <label className="flex items-center gap-2 text-sm font-semibold text-slate-200">
                                  <Calendar className="h-4 w-4 text-indigo-300"/> Set Deadline
                              </label>
                              <input 
                                  type="datetime-local" 
                                  className="w-full p-3.5 bg-white/[0.03] border border-white/[0.08] rounded-lg text-white focus:ring-2 focus:ring-indigo-400/40 focus:border-indigo-400/40 outline-none transition-all [color-scheme:dark]"
                                  value={deadline}
                                  onChange={(e) => setDeadline(e.target.value)}
                              />
                          </div>
                          <div className="flex gap-3 w-full md:w-auto">
                              <Button 
                                  variant="outline" 
                                  onClick={() => setShowEditMode(false)} 
                                  className="w-full md:w-auto h-12 px-6 bg-white/[0.02] border-white/[0.1] text-slate-200 hover:bg-white/[0.05] hover:text-white"
                              >
                                  Cancel
                              </Button>
                              <Button 
                                  onClick={handleAssignQuizToGroup} 
                                  className="w-full md:w-auto h-12 px-6 bg-gradient-to-r from-indigo-500 to-blue-600 hover:from-indigo-400 hover:to-blue-500 text-white font-semibold shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:shadow-[0_0_35px_rgba(99,102,241,0.55)] transition-all"
                              >
                                  Assign to Group <ChevronRight className="h-4 w-4 ml-1"/>
                              </Button>
                          </div>
                      </CardContent>
                  </Card>
              </div>
          </div>
      );
  }

  // ---------- QUIZ PLAYING VIEW ----------
  if (quizData.length > 0 && !showResult) {
      return (
        <div className="relative min-h-screen bg-gradient-to-br from-[#0a0f1e] via-[#08091a] to-[#050612] -m-6 p-6 md:p-10">
          {/* Ambient glows */}
          <div className="pointer-events-none absolute inset-0 overflow-hidden">
              <div className="absolute top-0 left-1/2 -translate-x-1/2 h-[500px] w-[600px] rounded-full bg-indigo-600/10 blur-[140px]" />
              <div className="absolute bottom-0 right-1/4 h-80 w-80 rounded-full bg-blue-500/10 blur-[120px]" />
          </div>

          <div className="relative max-w-3xl mx-auto space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
              {/* Header */}
              <div className="flex justify-between items-center">
                  <div className="flex items-center gap-3">
                      <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-indigo-500 to-blue-600 flex items-center justify-center shadow-[0_0_25px_rgba(99,102,241,0.4)]">
                          <Brain className="h-5 w-5 text-white"/>
                      </div>
                      <div>
                          <h2 className="text-xl font-bold text-white">AI Quiz</h2>
                          <p className="text-xs text-slate-400">Immersive Learning Mode</p>
                      </div>
                  </div>
                  <Badge className="bg-white/[0.03] border border-white/[0.1] text-slate-200 backdrop-blur-xl px-3 py-1.5 font-mono text-xs">
                      Q {currentQuestion + 1} / {quizData.length}
                  </Badge>
              </div>

              {/* Progress */}
              <div className="relative">
                  <Progress value={((currentQuestion) / quizData.length) * 100} className="h-1.5 bg-white/[0.04]" />
                  <div className="absolute inset-0 pointer-events-none rounded-full shadow-[0_0_15px_rgba(99,102,241,0.4)]"/>
              </div>
              
              {/* Question Card */}
              <Card className="relative overflow-hidden bg-white/[0.02] backdrop-blur-2xl border-white/[0.06] shadow-[0_0_60px_rgba(99,102,241,0.08)]">
                  <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/40 to-transparent"/>
                  <CardHeader className="pb-4">
                      <span className="text-[10px] font-bold text-indigo-300 uppercase tracking-[0.25em] mb-2">
                          Question {currentQuestion + 1}
                      </span>
                      <CardTitle className="text-xl md:text-2xl leading-relaxed text-white font-semibold">
                          <Latex>{quizData[currentQuestion].question}</Latex>
                      </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                      {quizData[currentQuestion].options.map((opt: string, i: number) => (
                          <div 
                              key={i} 
                              onClick={() => !isAnswerChecked && setSelectedOption(opt)} 
                              className={`group p-4 rounded-xl border cursor-pointer transition-all duration-300 flex justify-between items-center backdrop-blur-xl ${getOptionStyle(opt)}`}
                          >
                              <div className="flex items-center gap-3">
                                  <span className={`h-7 w-7 shrink-0 rounded-lg flex items-center justify-center text-xs font-bold transition-all ${
                                      !isAnswerChecked && selectedOption === opt 
                                          ? 'bg-indigo-500 text-white shadow-[0_0_15px_rgba(99,102,241,0.5)]' 
                                          : 'bg-white/[0.05] text-slate-300 border border-white/[0.08]'
                                  }`}>
                                      {String.fromCharCode(65 + i)}
                                  </span>
                                  <span className="font-medium text-sm text-white/90">
                                      <Latex>{opt}</Latex>
                                  </span>
                              </div>
                              {isAnswerChecked && opt === quizData[currentQuestion].correct_answer && (
                                  <CheckCircle className="text-emerald-400 h-5 w-5 shrink-0"/>
                              )}
                              {isAnswerChecked && selectedOption === opt && opt !== quizData[currentQuestion].correct_answer && (
                                  <XCircle className="text-rose-400 h-5 w-5 shrink-0"/>
                              )}
                          </div>
                      ))}
                      
                      <div className="pt-6 flex justify-between items-center">
                          <div className="text-xs text-slate-500">
                              Score: <span className="text-indigo-300 font-semibold">{score}</span> / {quizData.length}
                          </div>
                          <Button 
                              onClick={isAnswerChecked ? handleNext : handleCheckAnswer} 
                              disabled={!selectedOption} 
                              className="h-11 px-6 bg-gradient-to-r from-indigo-500 to-blue-600 hover:from-indigo-400 hover:to-blue-500 text-white font-semibold shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:shadow-[0_0_35px_rgba(99,102,241,0.6)] disabled:opacity-40 disabled:shadow-none transition-all"
                          >
                              {isAnswerChecked ? (currentQuestion + 1 === quizData.length ? "Finish Quiz" : "Next Question") : "Check Answer"}
                              <ChevronRight className="h-4 w-4 ml-1"/>
                          </Button>
                      </div>
                  </CardContent>
              </Card>
          </div>
        </div>
      );
  }

  // ---------- RESULT VIEW ----------
  if (showResult) {
      return (
          <div className="relative min-h-screen bg-gradient-to-br from-[#0a0f1e] via-[#08091a] to-[#050612] -m-6 flex items-center justify-center overflow-hidden">
              {/* Ambient */}
              <div className="pointer-events-none absolute inset-0">
                  <div className="absolute top-1/4 left-1/2 -translate-x-1/2 h-[500px] w-[500px] rounded-full bg-indigo-600/15 blur-[140px]" />
                  <div className="absolute bottom-0 right-1/4 h-80 w-80 rounded-full bg-amber-500/10 blur-[120px]" />
              </div>

              <div className="relative flex flex-col items-center justify-center text-center space-y-6 animate-in zoom-in-95 duration-500 px-6">
                  <div className="relative">
                      <div className="absolute inset-0 bg-amber-400/30 blur-[60px] rounded-full"/>
                      <div className="relative h-28 w-28 rounded-3xl bg-gradient-to-br from-amber-400 via-yellow-500 to-orange-500 flex items-center justify-center shadow-[0_0_60px_rgba(251,191,36,0.5)]">
                          <Trophy className="h-14 w-14 text-white drop-shadow-lg" />
                      </div>
                  </div>
                  <div className="space-y-3">
                      <h1 className="text-5xl md:text-6xl font-bold bg-gradient-to-r from-white via-indigo-100 to-blue-200 bg-clip-text text-transparent">
                          Quiz Completed!
                      </h1>
                      <p className="text-lg text-slate-400">Great work — here's how you did.</p>
                  </div>
                  <div className="bg-white/[0.02] backdrop-blur-2xl border border-white/[0.06] rounded-2xl px-10 py-6 shadow-[0_0_50px_rgba(99,102,241,0.1)]">
                      <p className="text-sm uppercase tracking-[0.25em] text-slate-500 mb-2">Final Score</p>
                      <p className="text-5xl font-bold">
                          <span className="bg-gradient-to-r from-indigo-300 to-blue-300 bg-clip-text text-transparent">{score}</span>
                          <span className="text-slate-600 text-3xl mx-2">/</span>
                          <span className="text-slate-300">{quizData.length}</span>
                      </p>
                  </div>
                  <Button 
                      onClick={() => { setQuizData([]); setShowResult(false); setIsConfigMode(false); }} 
                      className="h-12 px-8 bg-white/[0.03] border border-white/[0.1] text-white hover:bg-white/[0.06] hover:border-white/[0.2] backdrop-blur-xl transition-all"
                  >
                      <RefreshCw className="h-4 w-4 mr-2"/> Back to Dashboard
                  </Button>
              </div>
          </div>
      );
  }

  const isGroupCreator = selectedGroup?.creator === currentUser?.id || selectedGroup?.creator?.id === currentUser?.id;

  // ---------- MAIN DASHBOARD ----------
  return (
    <div className="relative min-h-screen bg-gradient-to-br from-[#0a0f1e] via-[#08091a] to-[#050612] -m-6 p-6 md:p-10">
      {/* Ambient glows */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="absolute -top-20 left-1/3 h-96 w-96 rounded-full bg-indigo-600/10 blur-[130px]" />
          <div className="absolute top-1/2 right-0 h-80 w-80 rounded-full bg-blue-500/8 blur-[130px]" />
      </div>

      <div className="relative space-y-10 animate-in fade-in duration-500 max-w-6xl mx-auto">
        {/* Hero Header */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-6 border-b border-white/[0.06]">
          <div>
            <div className="flex items-center gap-2 mb-3">
                <span className="inline-flex items-center gap-1.5 text-[10px] font-bold text-indigo-300 uppercase tracking-[0.25em] bg-indigo-500/10 border border-indigo-400/20 px-2.5 py-1 rounded-md">
                    <Sparkles className="h-3 w-3"/> AI-Powered
                </span>
            </div>
            <h1 className="text-4xl md:text-5xl font-bold bg-gradient-to-r from-white via-indigo-100 to-blue-200 bg-clip-text text-transparent">
                Quiz Generator
            </h1>
            <p className="text-slate-400 mt-2 max-w-xl">
                Test your knowledge or assign custom quizzes to your study groups. Powered by SparkLM AI.
            </p>
          </div>
        </div>

        {/* Generator Card */}
        <Card className="relative overflow-hidden bg-white/[0.02] backdrop-blur-2xl border border-dashed border-white/[0.1] hover:border-indigo-400/30 transition-all duration-500">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(99,102,241,0.08),transparent_60%)] pointer-events-none"/>
          <CardContent className="relative pt-8 pb-10">
            <div className="flex flex-col items-center justify-center text-center max-w-2xl mx-auto">
              {!isConfigMode ? (
                  <>
                      <div className="relative mb-5">
                          <div className="absolute inset-0 bg-indigo-500/40 blur-2xl rounded-full"/>
                          <div className="relative h-16 w-16 rounded-2xl bg-gradient-to-br from-indigo-500 to-blue-600 flex items-center justify-center shadow-[0_0_30px_rgba(99,102,241,0.5)]">
                              <Upload className="h-7 w-7 text-white" />
                          </div>
                      </div>
                      <h3 className="text-2xl font-bold text-white mb-2">Generate a New Quiz</h3>
                      <p className="text-sm text-slate-400 mb-6 max-w-md">
                        Upload study materials and let AI craft a customized quiz — perfect for solo practice or group assignments.
                      </p>
                      <Button 
                          onClick={() => setIsConfigMode(true)} 
                          className="h-11 px-6 bg-gradient-to-r from-indigo-500 to-blue-600 hover:from-indigo-400 hover:to-blue-500 text-white font-semibold shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:shadow-[0_0_35px_rgba(99,102,241,0.6)] transition-all"
                      >
                          <Brain className="h-4 w-4 mr-2" />
                          Create Quiz
                      </Button>
                  </>
              ) : (
                  <div className="w-full space-y-5 bg-white/[0.02] backdrop-blur-2xl p-6 md:p-8 rounded-2xl border border-white/[0.06] text-left animate-in slide-in-from-bottom-2 duration-300">
                      <div className="flex items-center gap-3 pb-3 border-b border-white/[0.05]">
                          <div className="h-9 w-9 rounded-lg bg-gradient-to-br from-indigo-500 to-blue-600 flex items-center justify-center shadow-[0_0_20px_rgba(99,102,241,0.4)]">
                              <Brain className="h-4 w-4 text-white"/>
                          </div>
                          <div>
                              <h4 className="text-white font-semibold">Configure Quiz</h4>
                              <p className="text-xs text-slate-500">Pick a source and let SparkLM handle the rest</p>
                          </div>
                      </div>

                      <div className="space-y-2">
                          <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Study Group</label>
                          <Select onValueChange={handleGroupChange}>
                              <SelectTrigger className="bg-white/[0.02] border-white/[0.08] text-white hover:bg-white/[0.04] hover:border-white/[0.15] h-11">
                                  <SelectValue placeholder="Pick a Group" />
                              </SelectTrigger>
                              <SelectContent className="bg-[#0a0f1e] border-white/[0.08] text-white">
                                  {groups.map(g => <SelectItem key={g.id} value={String(g.id)} className="focus:bg-white/[0.06] focus:text-white">{g.name}</SelectItem>)}
                              </SelectContent>
                          </Select>
                      </div>
                      <div className="space-y-2">
                          <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Document</label>
                          <Select onValueChange={setSelectedMaterialId} disabled={!selectedGroup}>
                              <SelectTrigger className="bg-white/[0.02] border-white/[0.08] text-white hover:bg-white/[0.04] hover:border-white/[0.15] disabled:opacity-40 h-11">
                                  <SelectValue placeholder={files.length === 0 ? "Select a Group First" : "Pick a File"} />
                              </SelectTrigger>
                              <SelectContent className="bg-[#0a0f1e] border-white/[0.08] text-white">
                                  {files.map(f => <SelectItem key={f.id} value={String(f.id)} className="focus:bg-white/[0.06] focus:text-white">{f.title}</SelectItem>)}
                              </SelectContent>
                          </Select>
                      </div>
                      <div className="space-y-2">
                          <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Topic <span className="text-slate-500 normal-case">(optional)</span></label>
                          <Input 
                              placeholder="e.g. Thermodynamics" 
                              value={topic} 
                              onChange={e => setTopic(e.target.value)} 
                              className="bg-white/[0.02] border-white/[0.08] text-white placeholder:text-slate-500 focus:border-indigo-400/40 focus:ring-indigo-400/40 h-11"
                          />
                      </div>
                      
                      <div className="flex flex-col sm:flex-row gap-3 pt-4">
                          <Button 
                              variant="outline" 
                              onClick={() => setIsConfigMode(false)} 
                              className="w-full sm:w-auto h-11 bg-white/[0.02] border-white/[0.08] text-slate-200 hover:bg-white/[0.05] hover:text-white"
                          >
                              Cancel
                          </Button>
                          
                          <Button 
                              onClick={() => handleGenerateQuiz('self')} 
                              disabled={loading || !selectedMaterialId} 
                              className="w-full h-11 bg-white/[0.04] border border-white/[0.1] text-white hover:bg-white/[0.08] hover:border-white/[0.2] font-semibold disabled:opacity-40 transition-all"
                          >
                              {loading ? <Loader2 className="animate-spin mr-2 h-4 w-4"/> : <Brain className="mr-2 h-4 w-4"/>} 
                              Self Study
                          </Button>

                          {isGroupCreator && (
                              <Button 
                                  onClick={() => handleGenerateQuiz('assign')} 
                                  disabled={loading || !selectedMaterialId} 
                                  className="w-full h-11 bg-gradient-to-r from-indigo-500 to-blue-600 hover:from-indigo-400 hover:to-blue-500 text-white font-semibold shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:shadow-[0_0_35px_rgba(99,102,241,0.6)] disabled:opacity-40 disabled:shadow-none transition-all"
                              >
                                  {loading ? <Loader2 className="animate-spin mr-2 h-4 w-4"/> : <Users className="mr-2 h-4 w-4"/>} 
                                  Assign to Group
                              </Button>
                          )}
                      </div>
                  </div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Pending Assignments */}
        {selectedGroup && assignedQuizzes.length > 0 && !isConfigMode && (
            <div className="animate-in slide-in-from-bottom-4 duration-500 space-y-5">
                <div className="flex items-center gap-3">
                    <div className="h-9 w-9 rounded-xl bg-gradient-to-br from-amber-500/20 to-orange-500/20 border border-amber-400/20 flex items-center justify-center">
                        <Calendar className="h-4 w-4 text-amber-300"/>
                    </div>
                    <div>
                        <h2 className="text-xl font-bold text-white">Pending Assignments</h2>
                        <p className="text-xs text-slate-500">Quizzes waiting for your attention</p>
                    </div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {assignedQuizzes.map((quiz) => (
                        <Card key={quiz.id} className="group relative overflow-hidden bg-white/[0.02] backdrop-blur-2xl border-white/[0.06] hover:border-amber-400/30 hover:bg-white/[0.03] transition-all duration-300">
                            <div className="absolute left-0 top-0 h-full w-[3px] bg-gradient-to-b from-amber-400 to-orange-500 shadow-[0_0_15px_rgba(251,191,36,0.4)]"/>
                            <CardContent className="p-5 flex flex-col justify-between h-full pl-6">
                                <div>
                                    <div className="flex justify-between items-start mb-3 gap-3">
                                        <h3 className="font-bold text-base text-white group-hover:text-amber-100 transition-colors">
                                            {quiz.name || quiz.title || "Assignment"}
                                        </h3>
                                        <Badge className="shrink-0 bg-amber-500/10 text-amber-300 border border-amber-400/20 text-[10px]">
                                            Due {new Date(quiz.deadline).toLocaleDateString()}
                                        </Badge>
                                    </div>
                                    <p className="text-xs text-slate-400 mb-5">
                                        Assigned by <span className="font-semibold text-slate-200">{quiz.creator_name}</span>
                                    </p>
                                </div>
                                <Button 
                                    onClick={() => handleStartAssignedQuiz(quiz)} 
                                    className="w-full h-10 bg-gradient-to-r from-indigo-500 to-blue-600 hover:from-indigo-400 hover:to-blue-500 text-white font-semibold shadow-[0_0_20px_rgba(99,102,241,0.35)] hover:shadow-[0_0_30px_rgba(99,102,241,0.55)] transition-all"
                                >
                                    Start Assignment <ChevronRight className="h-4 w-4 ml-1"/>
                                </Button>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            </div>
        )}
      </div>
    </div>
  );
}