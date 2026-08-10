import { useState, useEffect, useRef } from "react";
import { Card, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Send, Loader2, FileText, Bot, Sparkles, Users, MessageCircle } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { groupsAPI } from "@/services/api";
import api from "@/services/api"; 
import 'katex/dist/katex.min.css';

// Markdown Renderers
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';

export default function DoubtSolver() {
  const [groups, setGroups] = useState<any[]>([]);
  const [files, setFiles] = useState<any[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState("");
  const [selectedMaterialId, setSelectedMaterialId] = useState("");

  const [messages, setMessages] = useState<{id: number, sender: string, content: string, timestamp: string, citations?: string[]}[]>([
    {
      id: 1,
      sender: "ai",
      content: "Hi! Select a file below to start chatting with your document.",
      timestamp: "Just now",
    },
  ]);
  const [inputMessage, setInputMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const fetchAllGroups = async () => {
        try {
            const res = await groupsAPI.getAll();
            setGroups(res.data.results || res.data || []);
        } catch (err) {
            console.error("Failed to load groups", err);
        }
    };
    fetchAllGroups();
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
        scrollRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages]);

  const handleGroupChange = async (groupId: string) => {
    setSelectedGroupId(groupId);
    setFiles([]);
    try {
        const res = await groupsAPI.getMaterials(groupId);
        setFiles(res.data.results || res.data || []);
    } catch (err) {
        console.error(err);
    }
  };

  const handleSendMessage = async () => {
    if (!inputMessage.trim()) return;
    if (!selectedMaterialId) {
        alert("Please select a file first!");
        return;
    }

    const userText = inputMessage;
    const timestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    const newUserMessage = { id: Date.now(), sender: "user", content: userText, timestamp };
    setMessages((prev) => [...prev, newUserMessage]);
    setInputMessage("");
    setLoading(true);

    try {
      const res = await api.post('/ai/doubt/rag/', { 
        question: userText, 
        materialId: selectedMaterialId 
      });

      const aiAnswer = res.data.answer || "I couldn't find an answer in the document.";
      const aiCitations = res.data.citations || [];

      const aiResponse = {
        id: Date.now() + 1,
        sender: "ai",
        content: aiAnswer,
        citations: aiCitations,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };
      setMessages((prev) => [...prev, aiResponse]);

    } catch (err) {
      console.error(err);
      setMessages((prev) => [...prev, {
        id: Date.now() + 1, sender: "ai", content: "⚠️ Error: Check if Backend is running.", timestamp
      }]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  return (
    <div className="relative min-h-[calc(100vh-1px)] bg-gradient-to-br from-[#0a0f1e] via-[#08091a] to-[#050612] -m-6 p-6 md:p-8 flex flex-col">
      {/* Ambient glows */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="absolute -top-20 left-1/4 h-96 w-96 rounded-full bg-indigo-600/12 blur-[130px]" />
          <div className="absolute top-1/3 right-0 h-80 w-80 rounded-full bg-violet-500/10 blur-[130px]" />
          <div className="absolute bottom-0 left-0 h-72 w-72 rounded-full bg-blue-500/8 blur-[120px]" />
      </div>

      <div className="relative flex-1 flex flex-col animate-in fade-in duration-500 min-h-0 max-w-6xl mx-auto w-full">
        {/* Header */}
        <div className="pb-5 mb-5 border-b border-white/[0.06]">
          <div className="flex items-center gap-2 mb-2">
              <span className="inline-flex items-center gap-1.5 text-[10px] font-bold text-indigo-300 uppercase tracking-[0.25em] bg-indigo-500/10 border border-indigo-400/20 px-2.5 py-1 rounded-md">
                  <Sparkles className="h-3 w-3"/> RAG · Context-Aware AI
              </span>
          </div>
          <h1 className="text-3xl md:text-4xl font-bold bg-gradient-to-r from-white via-indigo-100 to-violet-200 bg-clip-text text-transparent flex items-center gap-3">
              <MessageCircle className="h-8 w-8 text-indigo-400" />
              Doubt Solver
          </h1>
          <p className="text-slate-400 mt-1.5 text-sm">Ask questions grounded in your own documents — powered by SparkLM AI.</p>
        </div>

        {/* Main Chat Interface */}
        <Card className="relative flex-1 flex flex-col min-h-0 bg-white/[0.02] backdrop-blur-2xl border-white/[0.06] shadow-[0_0_80px_rgba(99,102,241,0.06)] rounded-2xl overflow-hidden">
            
            {/* Ambient in-card glow */}
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(99,102,241,0.06),transparent_60%)] pointer-events-none"/>
            
            {/* Top Bar */}
            <div className="relative border-b border-white/[0.06] bg-white/[0.01] shrink-0 py-4 px-6 flex items-center justify-between backdrop-blur-xl">
              <div className="flex items-center gap-3">
                <div className="relative">
                    <div className="absolute inset-0 bg-indigo-500/40 blur-lg rounded-full"/>
                    <div className="relative h-10 w-10 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center shadow-[0_0_25px_rgba(99,102,241,0.5)]">
                        <Bot className="h-5 w-5 text-white" />
                    </div>
                </div>
                <div>
                  <CardTitle className="text-white text-base font-semibold">SparkLM Assistant</CardTitle>
                  <p className="text-[10px] text-slate-500 uppercase tracking-[0.2em] mt-0.5">Context-aware helper</p>
                </div>
              </div>
              {/* Status indicator */}
              <div className="flex items-center gap-2 bg-white/[0.03] border border-white/[0.06] rounded-full px-3 py-1.5">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]"></span>
                </span>
                <span className="text-[10px] text-emerald-300 uppercase tracking-widest font-semibold">Online</span>
              </div>
            </div>
            
            <div className="relative p-0 flex-1 flex flex-col min-h-0">
              
              {/* Messages Area */}
              <ScrollArea className="flex-1 p-6">
                <div className="space-y-7 max-w-4xl mx-auto">
                  {messages.map((message) => (
                    <div 
                        key={message.id} 
                        className={`flex gap-3.5 animate-in fade-in slide-in-from-bottom-2 duration-300 ${message.sender === "user" ? "flex-row-reverse" : ""}`}
                    >
                      
                      {/* Avatar */}
                      {message.sender === "ai" ? (
                          <div className="relative shrink-0">
                              <div className="absolute inset-0 bg-indigo-500/30 blur-md rounded-full"/>
                              <Avatar className="relative h-9 w-9 border border-indigo-400/30 bg-gradient-to-br from-indigo-500 to-violet-600 shadow-[0_0_15px_rgba(99,102,241,0.4)]">
                                  <AvatarFallback className="bg-transparent text-white">
                                      <Bot className="h-4 w-4" />
                                  </AvatarFallback>
                              </Avatar>
                          </div>
                      ) : (
                          <Avatar className="shrink-0 h-9 w-9 border border-white/[0.1] bg-white/[0.03]">
                              <AvatarFallback className="bg-transparent text-slate-200 font-bold text-[10px] tracking-widest">
                                  ME
                              </AvatarFallback>
                          </Avatar>
                      )}
                      
                      <div className={`flex-1 flex flex-col ${message.sender === "user" ? "items-end" : "items-start"} max-w-[85%]`}>
                        <div className={`relative inline-block px-5 py-3.5 overflow-x-auto ${
                            message.sender === "user" 
                            ? "bg-gradient-to-br from-indigo-500 to-blue-600 text-white rounded-2xl rounded-tr-sm shadow-[0_0_25px_rgba(99,102,241,0.25)]" 
                            : "bg-white/[0.02] backdrop-blur-2xl border border-white/[0.06] text-slate-100 rounded-2xl rounded-tl-sm shadow-[0_0_20px_rgba(0,0,0,0.3)]"
                          }`}>
                          
                          {message.sender === "ai" ? (
                              <div className="prose prose-sm prose-invert max-w-none 
                                              prose-headings:text-white prose-headings:font-semibold
                                              prose-p:text-slate-200 prose-p:leading-relaxed
                                              prose-strong:text-white prose-strong:font-semibold
                                              prose-a:text-indigo-300 prose-a:no-underline hover:prose-a:text-indigo-200
                                              prose-code:text-indigo-200 prose-code:bg-white/[0.05] prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none
                                              prose-pre:bg-black/60 prose-pre:border prose-pre:border-white/[0.08] prose-pre:backdrop-blur-xl
                                              prose-blockquote:border-l-indigo-400/60 prose-blockquote:text-slate-300
                                              prose-ul:text-slate-200 prose-ol:text-slate-200 prose-li:marker:text-indigo-400
                                              prose-hr:border-white/[0.08]
                                              prose-th:text-white prose-th:bg-white/[0.04] prose-th:border-white/[0.08] prose-th:p-2
                                              prose-td:text-slate-200 prose-td:border-white/[0.06] prose-td:p-2">
                                  <ReactMarkdown 
                                      remarkPlugins={[remarkGfm, remarkMath]} 
                                      rehypePlugins={[rehypeKatex]}
                                  >
                                      {message.content}
                                  </ReactMarkdown>
                                  
                                  {message.citations && message.citations.length > 0 && (
                                    <div className="mt-4 pt-3 border-t border-white/[0.08] space-y-2">
                                      <p className="text-[10px] font-bold text-indigo-300 uppercase tracking-widest flex items-center gap-1.5">
                                        <FileText className="h-3 w-3" /> Sources
                                      </p>
                                      {message.citations.map((citation, idx) => (
                                        <div key={idx} className="text-xs text-slate-400 bg-white/[0.03] p-2 rounded-lg border border-white/[0.05] italic">
                                          "{citation}"
                                        </div>
                                      ))}
                                    </div>
                                  )}
                              </div>
                          ) : (
                              <div className="text-sm whitespace-pre-wrap font-medium">{message.content}</div>
                          )}

                        </div>
                        <p className="text-[9px] text-slate-500 mt-1.5 px-1 tracking-[0.2em] uppercase font-mono">{message.timestamp}</p>
                      </div>
                    </div>
                  ))}
                  
                  {loading && (
                      <div className="flex gap-3.5 animate-in fade-in slide-in-from-bottom-2 duration-300">
                          <div className="relative shrink-0">
                              <div className="absolute inset-0 bg-indigo-500/30 blur-md rounded-full animate-pulse"/>
                              <Avatar className="relative h-9 w-9 border border-indigo-400/30 bg-gradient-to-br from-indigo-500 to-violet-600 shadow-[0_0_15px_rgba(99,102,241,0.4)]">
                                  <AvatarFallback className="bg-transparent text-white">
                                      <Bot className="h-4 w-4" />
                                  </AvatarFallback>
                              </Avatar>
                          </div>
                          <div className="bg-white/[0.02] backdrop-blur-2xl border border-white/[0.06] px-5 py-3.5 rounded-2xl rounded-tl-sm flex items-center gap-3 shadow-[0_0_20px_rgba(0,0,0,0.3)]">
                              <div className="flex gap-1">
                                  <span className="h-1.5 w-1.5 rounded-full bg-indigo-400 animate-bounce" style={{ animationDelay: '0ms' }}/>
                                  <span className="h-1.5 w-1.5 rounded-full bg-indigo-400 animate-bounce" style={{ animationDelay: '150ms' }}/>
                                  <span className="h-1.5 w-1.5 rounded-full bg-indigo-400 animate-bounce" style={{ animationDelay: '300ms' }}/>
                              </div>
                              <span className="text-sm font-medium text-slate-400">Analyzing document…</span>
                          </div>
                      </div>
                  )}
                  <div ref={scrollRef} />
                </div>
              </ScrollArea>

              {/* Bottom Bar: Inputs */}
              <div className="relative border-t border-white/[0.06] p-4 bg-white/[0.01] backdrop-blur-2xl shrink-0">
                <div className="flex flex-col md:flex-row gap-2.5 max-w-4xl mx-auto">
                  
                  {/* Selectors */}
                  <div className="flex gap-2 shrink-0">
                      <Select onValueChange={handleGroupChange}>
                          <SelectTrigger className="w-[160px] h-12 bg-white/[0.02] border-white/[0.08] text-slate-200 hover:bg-white/[0.04] hover:border-white/[0.15] focus:ring-indigo-400/40 rounded-xl text-sm transition-all">
                              <Users className="w-4 h-4 mr-1 text-slate-500 shrink-0"/>
                              <SelectValue placeholder="Group" />
                          </SelectTrigger>
                          <SelectContent className="bg-[#0a0f1e] border-white/[0.08] text-white">
                            {groups.map(g => <SelectItem key={g.id} value={String(g.id)} className="focus:bg-white/[0.06] focus:text-white">{g.name}</SelectItem>)}
                          </SelectContent>
                      </Select>

                      <Select onValueChange={setSelectedMaterialId} disabled={!selectedGroupId}>
                          <SelectTrigger className="w-[160px] h-12 bg-white/[0.02] border-white/[0.08] text-slate-200 hover:bg-white/[0.04] hover:border-white/[0.15] focus:ring-indigo-400/40 disabled:opacity-40 rounded-xl text-sm transition-all">
                               <FileText className="w-4 h-4 mr-1 text-slate-500 shrink-0"/>
                              <SelectValue placeholder="File" />
                          </SelectTrigger>
                          <SelectContent className="bg-[#0a0f1e] border-white/[0.08] text-white">
                            {files.map(f => <SelectItem key={f.id} value={String(f.id)} className="focus:bg-white/[0.06] focus:text-white">{f.title}</SelectItem>)}
                          </SelectContent>
                      </Select>
                  </div>

                  {/* Input Field */}
                  <div className="flex-1 flex gap-2">
                      <div className="flex-1 relative group">
                          <Input
                            placeholder={selectedMaterialId ? "Ask your question..." : "Select a file to start chatting"}
                            value={inputMessage}
                            onChange={(e) => setInputMessage(e.target.value)}
                            onKeyDown={handleKeyPress}
                            disabled={!selectedMaterialId || loading}
                            className="w-full h-12 rounded-xl bg-white/[0.02] border-white/[0.08] text-white focus-visible:ring-indigo-400/40 focus-visible:border-indigo-400/40 placeholder:text-slate-500 text-sm pr-10 disabled:opacity-40 transition-all"
                          />
                          {selectedMaterialId && (
                              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[9px] font-mono text-slate-500 uppercase tracking-widest pointer-events-none opacity-0 group-focus-within:opacity-100 transition-opacity">
                                  ↵ send
                              </span>
                          )}
                      </div>
                      <Button 
                        onClick={handleSendMessage} 
                        disabled={!inputMessage.trim() || loading} 
                        className="h-12 w-12 rounded-xl bg-gradient-to-br from-indigo-500 to-blue-600 hover:from-indigo-400 hover:to-blue-500 shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:shadow-[0_0_35px_rgba(99,102,241,0.6)] disabled:opacity-40 disabled:shadow-none transition-all flex items-center justify-center p-0 shrink-0"
                      >
                        {loading ? <Loader2 className="h-5 w-5 animate-spin text-white"/> : <Send className="h-5 w-5 text-white ml-0.5" />}
                      </Button>
                  </div>

                </div>
                
                {/* Subtle hint */}
                {!selectedMaterialId && (
                    <p className="text-[10px] text-slate-500 text-center mt-2.5 uppercase tracking-widest">
                        Select a study group &amp; file to activate the assistant
                    </p>
                )}
              </div>
            </div>
        </Card>
      </div>
    </div>
  );
}
