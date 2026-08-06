import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  User,
  Bell,
  Shield,
  Globe,
  Save,
  KeyRound,
  Sparkles,
  Clock,
} from "lucide-react";
import { toast } from "sonner";
import { useState, useEffect } from "react";
import { userAPI } from "@/services/api";

export default function Settings() {
  const [profile, setProfile] = useState({ first_name: "", last_name: "", email: "", bio: "", email_alerts: true });

  // Was a raw fetch to the RELATIVE path "/api/settings/profile/". On Vercel
  // that resolved against the SPA's own origin, where the catch-all rewrite
  // in vercel.json serves index.html with HTTP 200 — so this parsed the
  // app's own HTML as JSON, threw, and was swallowed. This page has been
  // broken in production for three independent reasons: that, `Bearer null`
  // from a localStorage key nothing ever wrote, and a backend 500 on
  // profile.bio. The first two are fixed; the third was fixed in M5 Phase 1.
  useEffect(() => {
    userAPI.getSettings()
      .then(({ data }) => {
        if (data.email !== undefined) setProfile(data);
      })
      .catch(console.error);
  }, []);

  const handleSaveProfile = async () => {
    try {
      await userAPI.updateSettings(profile);
      toast.success("Profile saved");
    } catch (e) {
      // This catch was always here and always correct — it simply never
      // fired. `fetch` resolves on any HTTP status, so a 400 (duplicate or
      // malformed email) or a 500 still reached toast.success and told the
      // user "Profile saved" when nothing had been saved. axios rejects on
      // non-2xx, so the existing error path now actually runs. No new code:
      // changing the transport is what fixed the lie.
      toast.error("Failed to save profile");
    }
  };

  const handleToggleEmail = async (checked: boolean) => {
    setProfile({ ...profile, email_alerts: checked });
    if (checked) {
      toast("Sending test email to verify connection...");
      userAPI.sendTestEmail()
        .then(({ data }) => {
          if (data.status === "Email sent successfully!") {
              toast.success(data.status);
          } else {
              toast.error(data.message || "Failed to send email. Check SMTP settings.");
          }
        })
        // TestEmailView returns 500 with {status:"error", message: <reason>}
        // when SMTP fails. `fetch` resolved on that, so the else branch above
        // read `data.message` and showed the real reason. axios REJECTS on
        // 500, which would leave the else branch unreachable, the reason
        // lost, and the rejection unhandled. Reading the same field off
        // err.response.data keeps the user-visible behaviour identical.
        .catch((err) => {
          toast.error(
            err?.response?.data?.message ||
            "Failed to send email. Check SMTP settings."
          );
        });
    }
  };

  // Shared premium tokens
  const glassCard =
    "relative overflow-hidden bg-white/[0.02] backdrop-blur-2xl border-white/[0.06] " +
    "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]";

  const sleekInput =
    "h-11 bg-white/[0.02] backdrop-blur-xl border-white/[0.08] text-white " +
    "placeholder:text-slate-500 focus-visible:ring-1 focus-visible:ring-indigo-400/40 " +
    "focus-visible:border-indigo-400/40 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.03)] " +
    "transition-all duration-200";

  const tabTrigger =
    "relative rounded-lg text-sm font-medium text-slate-400 " +
    "data-[state=active]:text-white " +
    "data-[state=active]:bg-gradient-to-br data-[state=active]:from-indigo-500 data-[state=active]:to-violet-600 " +
    "data-[state=active]:shadow-[0_0_20px_rgba(99,102,241,0.35)] " +
    "transition-all duration-300";

  const labelCls =
    "text-[10px] uppercase tracking-[0.2em] text-slate-400 font-semibold";

  const primaryGlowBtn =
    "h-11 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 hover:from-indigo-400 hover:to-violet-500 " +
    "text-white font-semibold shadow-[0_0_25px_rgba(99,102,241,0.4)] hover:shadow-[0_0_35px_rgba(99,102,241,0.6)] " +
    "transition-all duration-300";

  const premiumSwitch =
    "data-[state=checked]:bg-gradient-to-r data-[state=checked]:from-indigo-500 data-[state=checked]:to-violet-600 " +
    "data-[state=checked]:shadow-[0_0_15px_rgba(99,102,241,0.5)] " +
    "data-[state=unchecked]:bg-white/[0.06] border border-white/[0.08]";

  // Reusable card header (pure JSX — no logic)
  const CardTopHeader = ({ icon: Icon, title, description, accent = "indigo" }: any) => {
    const accents: Record<string, string> = {
      indigo: "from-indigo-500 to-violet-600 shadow-[0_0_25px_rgba(99,102,241,0.4)]",
      violet: "from-violet-500 to-purple-600 shadow-[0_0_25px_rgba(139,92,246,0.4)]",
      emerald: "from-emerald-500 to-teal-600 shadow-[0_0_25px_rgba(16,185,129,0.4)]",
      amber: "from-amber-500 to-orange-600 shadow-[0_0_25px_rgba(251,146,60,0.4)]",
    };
    const rails: Record<string, string> = {
      indigo: "via-indigo-400/50",
      violet: "via-violet-400/50",
      emerald: "via-emerald-400/50",
      amber: "via-amber-400/50",
    };
    return (
      <>
        <div className={`absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent ${rails[accent]} to-transparent`} />
        <div className="relative border-b border-white/[0.06] px-6 py-4 flex items-center gap-3">
          <div className={`h-10 w-10 rounded-xl bg-gradient-to-br ${accents[accent]} flex items-center justify-center shrink-0`}>
            <Icon className="h-4 w-4 text-white" />
          </div>
          <div className="min-w-0">
            <h3 className="text-white font-semibold text-sm">{title}</h3>
            <p className="text-xs text-slate-400 truncate">{description}</p>
          </div>
        </div>
      </>
    );
  };

  return (
    <div
      data-testid="settings-page"
      className="relative min-h-screen bg-gradient-to-br from-[#0a0f1e] via-[#08091a] to-[#050612] -m-6 p-6 md:p-10 animate-in fade-in duration-500"
    >
      {/* Ambient glows */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-40 -left-20 h-[420px] w-[420px] rounded-full bg-indigo-600/12 blur-[130px]" />
        <div className="absolute top-32 right-0 h-80 w-80 rounded-full bg-violet-500/10 blur-[130px]" />
        <div className="absolute bottom-0 left-1/3 h-72 w-72 rounded-full bg-blue-500/8 blur-[120px]" />
      </div>

      <div className="relative max-w-5xl mx-auto w-full space-y-8">
        {/* HERO HEADER */}
        <div
          data-testid="settings-hero"
          className={`animate-in slide-in-from-bottom-4 fade-in duration-500 rounded-2xl border p-8 ${glassCard}`}
        >
          <div
            className="absolute inset-0 opacity-[0.035] pointer-events-none"
            style={{
              backgroundImage:
                "linear-gradient(to right, white 1px, transparent 1px), linear-gradient(to bottom, white 1px, transparent 1px)",
              backgroundSize: "32px 32px",
            }}
          />
          <div className="absolute -top-24 -right-24 h-72 w-72 rounded-full bg-indigo-500/20 blur-3xl pointer-events-none" />
          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/40 to-transparent" />

          <div className="relative z-10 max-w-2xl">
            <div className="inline-flex items-center gap-1.5 text-[10px] font-bold text-indigo-300 uppercase tracking-[0.25em] bg-indigo-500/10 border border-indigo-400/20 px-2.5 py-1 rounded-md">
              <Sparkles className="h-3 w-3" /> Account preferences
            </div>
            <h1 className="mt-4 text-4xl sm:text-5xl font-bold tracking-tight bg-gradient-to-r from-white via-indigo-100 to-violet-200 bg-clip-text text-transparent">
              Settings
            </h1>
            <p className="mt-3 text-base md:text-lg text-slate-400">
              Manage your account, security, and platform preferences.
            </p>
          </div>
        </div>

        {/* TABS */}
        <Tabs defaultValue="profile" className="w-full">
          <TabsList
            data-testid="settings-tabs"
            className="grid w-full grid-cols-4 h-12 p-1 rounded-xl bg-white/[0.02] backdrop-blur-2xl border border-white/[0.06]"
          >
            <TabsTrigger data-testid="tab-profile" value="profile" className={tabTrigger}>
              <User className="h-4 w-4 sm:mr-2" />
              <span className="hidden sm:inline">Profile</span>
            </TabsTrigger>
            <TabsTrigger data-testid="tab-notifications" value="notifications" className={tabTrigger}>
              <Bell className="h-4 w-4 sm:mr-2" />
              <span className="hidden sm:inline">Notifications</span>
            </TabsTrigger>
            <TabsTrigger data-testid="tab-security" value="security" className={tabTrigger}>
              <Shield className="h-4 w-4 sm:mr-2" />
              <span className="hidden sm:inline">Security</span>
            </TabsTrigger>
            <TabsTrigger data-testid="tab-language" value="language" className={tabTrigger}>
              <Globe className="h-4 w-4 sm:mr-2" />
              <span className="hidden sm:inline">Language</span>
            </TabsTrigger>
          </TabsList>

          {/* PROFILE */}
          <TabsContent
            value="profile"
            className="mt-6 space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300"
          >
            <Card data-testid="profile-card" className={glassCard}>
              <CardTopHeader icon={User} title="Profile Information" description="Update your personal details" accent="indigo" />
              <CardContent className="pt-6 space-y-5">
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <Label className={labelCls}>First Name</Label>
                    <Input
                      data-testid="profile-first-name"
                      placeholder="Your Name"
                      value={profile.first_name}
                      onChange={(e) => setProfile({ ...profile, first_name: e.target.value })}
                      className={sleekInput}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label className={labelCls}>Last Name</Label>
                    <Input
                      data-testid="profile-last-name"
                      placeholder="Your Last Name"
                      value={profile.last_name}
                      onChange={(e) => setProfile({ ...profile, last_name: e.target.value })}
                      className={sleekInput}
                    />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label className={labelCls}>Email</Label>
                  <Input
                    data-testid="profile-email"
                    type="email"
                    placeholder="email@example.com"
                    value={profile.email}
                    onChange={(e) => setProfile({ ...profile, email: e.target.value })}
                    className={sleekInput}
                  />
                </div>
                <div className="space-y-2">
                  <Label className={labelCls}>Bio</Label>
                  <Input
                    data-testid="profile-bio"
                    placeholder="Tell us about yourself..."
                    value={profile.bio}
                    onChange={(e) => setProfile({ ...profile, bio: e.target.value })}
                    className={sleekInput}
                  />
                </div>
                <Button
                  data-testid="profile-save-btn"
                  onClick={handleSaveProfile}
                  className={primaryGlowBtn}
                >
                  <Save className="h-4 w-4 mr-2" />
                  Save Changes
                </Button>
              </CardContent>
            </Card>
          </TabsContent>

          {/* NOTIFICATIONS */}
          <TabsContent
            value="notifications"
            className="mt-6 space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300"
          >
            <Card data-testid="notifications-card" className={glassCard}>
              <CardTopHeader icon={Bell} title="Notifications" description="Configure your email and push notification preferences" accent="violet" />
              <CardContent className="pt-6 space-y-3">
                {/* Email Alerts row */}
                <div className="group relative overflow-hidden flex items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-xl px-4 py-3.5 hover:border-indigo-400/30 hover:bg-white/[0.03] transition-all">
                  <div className="absolute left-0 top-0 h-full w-[3px] bg-gradient-to-b from-indigo-400 to-violet-500 opacity-0 group-hover:opacity-70 shadow-[0_0_15px_rgba(99,102,241,0.5)] transition-opacity" />
                  <div className="flex items-center gap-3">
                    <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-indigo-500/20 to-violet-500/20 border border-indigo-400/20 flex items-center justify-center backdrop-blur-xl">
                      <Bell className="h-4 w-4 text-indigo-300" />
                    </div>
                    <div>
                      <Label className="text-sm font-semibold text-white">
                        Email Alerts
                      </Label>
                      <p className="text-xs text-slate-400 mt-0.5">
                        Receive important updates by email
                      </p>
                    </div>
                  </div>
                  <Switch
                    data-testid="switch-email-alerts"
                    checked={profile.email_alerts}
                    onCheckedChange={handleToggleEmail}
                    className={premiumSwitch}
                  />
                </div>

                {/* Study Reminders row */}
                <div className="group relative overflow-hidden flex items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-xl px-4 py-3.5 hover:border-emerald-400/30 hover:bg-white/[0.03] transition-all">
                  <div className="absolute left-0 top-0 h-full w-[3px] bg-gradient-to-b from-emerald-400 to-teal-500 opacity-0 group-hover:opacity-70 shadow-[0_0_15px_rgba(16,185,129,0.5)] transition-opacity" />
                  <div className="flex items-center gap-3">
                    <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-emerald-500/20 to-teal-500/20 border border-emerald-400/20 flex items-center justify-center backdrop-blur-xl">
                      <Clock className="h-4 w-4 text-emerald-300" />
                    </div>
                    <div>
                      <Label className="text-sm font-semibold text-white">
                        Study Reminders
                      </Label>
                      <p className="text-xs text-slate-400 mt-0.5">
                        Daily nudges to keep your streak alive
                      </p>
                    </div>
                  </div>
                  <Switch
                    data-testid="switch-study-reminders"
                    defaultChecked
                    className={premiumSwitch}
                  />
                </div>

                <Button
                  data-testid="notifications-save-btn"
                  onClick={handleSaveProfile}
                  className={`w-full mt-3 ${primaryGlowBtn}`}
                >
                  <Save className="h-4 w-4 mr-2" />
                  Save Preferences
                </Button>
              </CardContent>
            </Card>
          </TabsContent>

          {/* SECURITY */}
          <TabsContent
            value="security"
            className="mt-6 space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300"
          >
            <Card data-testid="security-card" className={glassCard}>
              <CardTopHeader icon={Shield} title="Security" description="Update your password and account safety" accent="emerald" />
              <CardContent className="pt-6 space-y-5">
                <div className="space-y-2">
                  <Label className={labelCls}>Current Password</Label>
                  <Input
                    data-testid="security-current-password"
                    type="password"
                    placeholder="••••••••••••"
                    className={`${sleekInput} font-mono tracking-wider`}
                  />
                </div>
                <div className="space-y-2">
                  <Label className={labelCls}>New Password</Label>
                  <Input
                    data-testid="security-new-password"
                    type="password"
                    placeholder="••••••••••••"
                    className={`${sleekInput} font-mono tracking-wider`}
                  />
                </div>

                {/* Security tip */}
                <div className="flex items-center gap-2 rounded-lg border border-emerald-400/25 bg-gradient-to-r from-emerald-500/[0.08] via-emerald-500/[0.04] to-transparent backdrop-blur-xl px-3.5 py-2.5 shadow-[0_0_20px_rgba(16,185,129,0.1)]">
                  <div className="h-6 w-6 rounded-md bg-emerald-500/15 border border-emerald-400/25 flex items-center justify-center shrink-0">
                    <Shield className="h-3 w-3 text-emerald-300" />
                  </div>
                  <span className="text-xs text-emerald-200 font-medium">
                    Use 12+ characters with symbols &amp; numbers for stronger security
                  </span>
                </div>

                <Button data-testid="security-update-btn" className={primaryGlowBtn}>
                  <KeyRound className="h-4 w-4 mr-2" />
                  Update Password
                </Button>
              </CardContent>
            </Card>
          </TabsContent>

          {/* LANGUAGE */}
          <TabsContent
            value="language"
            className="mt-6 space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300"
          >
            <Card data-testid="language-card" className={glassCard}>
              <CardTopHeader icon={Globe} title="Language & Region" description="Choose your preferred language" accent="amber" />
              <CardContent className="pt-6 space-y-5">
                <div className="space-y-2">
                  <Label className={labelCls}>Language</Label>
                  <Select defaultValue="en">
                    <SelectTrigger
                      data-testid="language-select-trigger"
                      className="h-11 bg-white/[0.02] backdrop-blur-xl border-white/[0.08] text-white hover:bg-white/[0.04] hover:border-white/[0.15] focus:ring-1 focus:ring-indigo-400/40 focus:border-indigo-400/40 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.03)] transition-all"
                    >
                      <SelectValue placeholder="Select" />
                    </SelectTrigger>
                    <SelectContent className="bg-[#0a0f1e] backdrop-blur-2xl border-white/[0.08] text-white">
                      <SelectItem value="en" className="focus:bg-white/[0.06] focus:text-white">English</SelectItem>
                      <SelectItem value="es" className="focus:bg-white/[0.06] focus:text-white">Spanish</SelectItem>
                      <SelectItem value="fr" className="focus:bg-white/[0.06] focus:text-white">French</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <Button data-testid="language-save-btn" className={`w-full ${primaryGlowBtn}`}>
                  <Save className="h-4 w-4 mr-2" />
                  Save Region
                </Button>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}