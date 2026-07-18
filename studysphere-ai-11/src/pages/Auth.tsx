import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useNavigate } from "react-router-dom";
import logo from "@/assets/logo.jpeg";
import { authAPI } from "@/services/api";
import { Sparkles, ArrowRight, User, Lock, Mail } from "lucide-react";

export default function Auth() {
  const navigate = useNavigate();
  
  const [loginUsername, setLoginUsername] = useState(""); 
  const [loginPassword, setLoginPassword] = useState("");
  
  const [signupName, setSignupName] = useState("");
  const [signupEmail, setSignupEmail] = useState("");
  const [signupPassword, setSignupPassword] = useState("");
  const [signupConfirmPassword, setSignupConfirmPassword] = useState("");

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!loginUsername || loginUsername === "") {
        alert("Please enter your username.");
        return;
    }

    try {
      const response = await authAPI.login(loginUsername, loginPassword);
      if (response.access) {
        window.location.href = "/";
      }
    } catch (error) {
      console.error("LOGIN FAILED. Error details:", error);
      alert("Login Failed! Please check your credentials.");
    }
  };

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    if (signupPassword !== signupConfirmPassword) {
      alert("Passwords do not match!");
      return;
    }
    
    try {
      await authAPI.signup(signupName, signupEmail, signupPassword);
      alert("Account created successfully! Please log in.");
    } catch (error) {
      console.error("Signup failed:", error);
      alert("Signup failed. That username might be taken.");
    }
  };

  // shared input class
  const inputCls =
    "h-11 pl-10 bg-white/[0.02] backdrop-blur-xl border-white/[0.08] text-white " +
    "placeholder:text-slate-500 focus-visible:ring-1 focus-visible:ring-indigo-400/40 " +
    "focus-visible:border-indigo-400/40 transition-all";

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-[#0a0f1e] via-[#08091a] to-[#050612] p-4 relative overflow-hidden">
      {/* Ambient glow orbs */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute top-1/4 left-1/4 h-[500px] w-[500px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-indigo-600/20 blur-[130px] animate-pulse" style={{ animationDuration: '8s' }}/>
        <div className="absolute bottom-1/4 right-1/4 h-[500px] w-[500px] translate-x-1/2 translate-y-1/2 rounded-full bg-violet-600/20 blur-[130px] animate-pulse" style={{ animationDuration: '10s', animationDelay: '2s' }}/>
        <div className="absolute top-1/2 left-1/2 h-[300px] w-[300px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-blue-500/10 blur-[100px]"/>
      </div>

      {/* Grid overlay */}
      <div
        className="absolute inset-0 opacity-[0.025] pointer-events-none"
        style={{
          backgroundImage:
            "linear-gradient(to right, white 1px, transparent 1px), linear-gradient(to bottom, white 1px, transparent 1px)",
          backgroundSize: "48px 48px",
        }}
      />

      {/* Subtle top branding */}
      <div className="absolute top-8 left-1/2 -translate-x-1/2 flex items-center gap-2 text-[10px] font-bold text-indigo-300 uppercase tracking-[0.3em] opacity-70">
        <span className="h-px w-8 bg-gradient-to-r from-transparent to-indigo-400/50"/>
        SparkLM · v1.0
        <span className="h-px w-8 bg-gradient-to-l from-transparent to-indigo-400/50"/>
      </div>

      <Card className="w-full max-w-md relative z-10 bg-white/[0.02] backdrop-blur-2xl border-white/[0.06] shadow-[0_0_80px_rgba(99,102,241,0.15)] overflow-hidden animate-in fade-in zoom-in-95 duration-500">
        {/* Top gradient hairline */}
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/60 to-transparent"/>
        {/* Inner radial glow */}
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(99,102,241,0.1),transparent_60%)] pointer-events-none"/>

        <div className="relative">
          {/* HERO / BRAND */}
          <div className="text-center pt-8 pb-6 px-6 space-y-4">
            <div className="relative mx-auto w-fit">
              <div className="absolute inset-0 bg-indigo-500/40 blur-2xl rounded-3xl"/>
              <div className="relative h-20 w-20 rounded-2xl overflow-hidden ring-1 ring-indigo-400/30 shadow-[0_0_30px_rgba(99,102,241,0.5)] bg-gradient-to-br from-indigo-500/20 to-violet-600/20">
                <img src={logo} alt="SparkLM" className="h-full w-full object-cover" />
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="inline-flex items-center gap-1.5 text-[9px] font-bold text-indigo-300 uppercase tracking-[0.3em] bg-indigo-500/10 border border-indigo-400/20 px-2.5 py-1 rounded-full">
                <Sparkles className="h-2.5 w-2.5" /> AI-Powered Learning
              </div>
              <h1 className="text-4xl font-bold tracking-tight bg-gradient-to-r from-white via-indigo-100 to-violet-200 bg-clip-text text-transparent">
                SparkLM
              </h1>
              <p className="text-[10px] text-slate-400 uppercase tracking-[0.25em] font-medium">
                Virtual Study Group Platform
              </p>
            </div>
          </div>

          <CardContent className="pb-8">
            <Tabs defaultValue="login" className="w-full">
              <TabsList className="grid w-full grid-cols-2 mb-6 h-11 p-1 rounded-xl bg-white/[0.02] backdrop-blur-2xl border border-white/[0.06]">
                <TabsTrigger 
                  value="login" 
                  className="rounded-lg text-sm font-semibold text-slate-400 data-[state=active]:bg-gradient-to-br data-[state=active]:from-indigo-500 data-[state=active]:to-violet-600 data-[state=active]:text-white data-[state=active]:shadow-[0_0_20px_rgba(99,102,241,0.4)] transition-all"
                >
                  Sign In
                </TabsTrigger>
                <TabsTrigger 
                  value="signup" 
                  className="rounded-lg text-sm font-semibold text-slate-400 data-[state=active]:bg-gradient-to-br data-[state=active]:from-indigo-500 data-[state=active]:to-violet-600 data-[state=active]:text-white data-[state=active]:shadow-[0_0_20px_rgba(99,102,241,0.4)] transition-all"
                >
                  Create Account
                </TabsTrigger>
              </TabsList>
              
              {/* LOGIN TAB */}
              <TabsContent value="login" className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <form onSubmit={handleLogin} className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="login-username" className="text-[10px] font-bold text-slate-400 uppercase tracking-[0.2em]">Username</Label>
                    <div className="relative">
                      <User className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500 pointer-events-none"/>
                      <Input
                        id="login-username"
                        type="text"
                        placeholder="Enter your username"
                        value={loginUsername}
                        onChange={(e) => setLoginUsername(e.target.value)}
                        required
                        className={inputCls}
                      />
                    </div>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="login-password" className="text-[10px] font-bold text-slate-400 uppercase tracking-[0.2em]">Password</Label>
                    <div className="relative">
                      <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500 pointer-events-none"/>
                      <Input
                        id="login-password"
                        type="password"
                        placeholder="••••••••"
                        value={loginPassword}
                        onChange={(e) => setLoginPassword(e.target.value)}
                        required
                        className={`${inputCls} font-mono tracking-widest`}
                      />
                    </div>
                  </div>

                  <Button 
                    type="submit" 
                    className="group w-full h-11 bg-gradient-to-r from-indigo-500 to-violet-600 hover:from-indigo-400 hover:to-violet-500 text-white font-semibold shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:shadow-[0_0_40px_rgba(99,102,241,0.65)] transition-all"
                  >
                    Sign In
                    <ArrowRight className="h-4 w-4 ml-1.5 group-hover:translate-x-0.5 transition-transform"/>
                  </Button>

                  <p className="text-center text-[10px] text-slate-500 uppercase tracking-widest pt-2">
                    Secure &amp; encrypted authentication
                  </p>
                </form>
              </TabsContent>

              {/* SIGNUP TAB */}
              <TabsContent value="signup" className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
                <form onSubmit={handleSignup} className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="signup-name" className="text-[10px] font-bold text-slate-400 uppercase tracking-[0.2em]">Username</Label>
                    <div className="relative">
                      <User className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500 pointer-events-none"/>
                      <Input
                        id="signup-name"
                        type="text"
                        placeholder="Choose a username"
                        value={signupName}
                        onChange={(e) => setSignupName(e.target.value)}
                        required
                        className={inputCls}
                      />
                    </div>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="signup-email" className="text-[10px] font-bold text-slate-400 uppercase tracking-[0.2em]">Email</Label>
                    <div className="relative">
                      <Mail className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500 pointer-events-none"/>
                      <Input
                        id="signup-email"
                        type="email"
                        placeholder="student@university.edu"
                        value={signupEmail}
                        onChange={(e) => setSignupEmail(e.target.value)}
                        required
                        className={inputCls}
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-1 gap-4">
                    <div className="space-y-2">
                      <Label htmlFor="signup-password" className="text-[10px] font-bold text-slate-400 uppercase tracking-[0.2em]">Password</Label>
                      <div className="relative">
                        <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500 pointer-events-none"/>
                        <Input
                          id="signup-password"
                          type="password"
                          placeholder="••••••••"
                          value={signupPassword}
                          onChange={(e) => setSignupPassword(e.target.value)}
                          required
                          className={`${inputCls} font-mono tracking-widest`}
                        />
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="signup-confirm-password" className="text-[10px] font-bold text-slate-400 uppercase tracking-[0.2em]">Confirm Password</Label>
                      <div className="relative">
                        <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500 pointer-events-none"/>
                        <Input
                          id="signup-confirm-password"
                          type="password"
                          placeholder="••••••••"
                          value={signupConfirmPassword}
                          onChange={(e) => setSignupConfirmPassword(e.target.value)}
                          required
                          className={`${inputCls} font-mono tracking-widest`}
                        />
                      </div>
                    </div>
                  </div>

                  <Button 
                    type="submit" 
                    className="group w-full h-11 bg-gradient-to-r from-indigo-500 to-violet-600 hover:from-indigo-400 hover:to-violet-500 text-white font-semibold shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:shadow-[0_0_40px_rgba(99,102,241,0.65)] transition-all"
                  >
                    Create Account
                    <ArrowRight className="h-4 w-4 ml-1.5 group-hover:translate-x-0.5 transition-transform"/>
                  </Button>

                  <p className="text-center text-[10px] text-slate-500 uppercase tracking-widest pt-2">
                    By continuing you agree to our terms
                  </p>
                </form>
              </TabsContent>
            </Tabs>
          </CardContent>
        </div>
      </Card>

      {/* Subtle bottom brand */}
      <div className="absolute bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-2 text-[9px] text-slate-600 uppercase tracking-[0.3em]">
        Powered by <span className="text-indigo-400 font-semibold">SparkLM AI</span>
      </div>
    </div>
  );
}