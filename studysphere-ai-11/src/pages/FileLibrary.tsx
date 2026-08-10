import { useState, useEffect, useRef } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Upload, Search, FileText, Image as ImageIcon,
  FileCode, File, Loader2, Trash2, Download, Eye,
  Sparkles, FolderOpen,
} from "lucide-react";
import VisualSearch from "../components/VisualSearch";
import api, { groupsAPI } from "@/services/api";

// ── Types ────────────────────────────────────────────────────
interface StudyGroup {
  id: number;
  name: string;
}

interface StudyMaterial {
  id: number;
  title: string;
  file: string;
  upload_date: string;
  uploaded_by: { username: string };
  study_group: { id: number; name: string };
}

// ── Helpers ──────────────────────────────────────────────────
function fileIcon(url: string) {
  const ext = url?.split('.').pop()?.toLowerCase() || '';
  if (['jpg', 'jpeg', 'png', 'gif', 'webp'].includes(ext)) return <ImageIcon className="w-5 h-5 text-blue-300" />;
  if (ext === 'pdf') return <FileText className="w-5 h-5 text-rose-300" />;
  if (['py', 'js', 'ts', 'java', 'cpp'].includes(ext)) return <FileCode className="w-5 h-5 text-emerald-300" />;
  return <File className="w-5 h-5 text-slate-400" />;
}

function isImage(url: string) {
  const ext = url?.split('.').pop()?.toLowerCase() || '';
  return ['jpg', 'jpeg', 'png', 'gif', 'webp'].includes(ext);
}

function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

// ── Component ─────────────────────────────────────────────────
export default function FileLibrary() {
  const [searchQuery, setSearchQuery] = useState("");
  const [showVisualSearch, setShowVisualSearch] = useState(false);

  // Groups & files
  const [groups, setGroups] = useState<StudyGroup[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<string>("");
  const [files, setFiles] = useState<StudyMaterial[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(false);

  // Upload
  const [uploading, setUploading] = useState(false);
  const [uploadTitle, setUploadTitle] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Fetch groups on mount ────────────────────────────────────
  useEffect(() => {
    const fetchGroups = async () => {
      try {
        const res = await groupsAPI.getAll();
        setGroups(res.data.results || res.data || []);
      } catch (err) {
        console.error("Failed to load groups", err);
      }
    };
    fetchGroups();
  }, []);

  // ── Fetch files when group changes ──────────────────────────
  useEffect(() => {
    if (!selectedGroupId) return;
    const fetchFiles = async () => {
      setLoadingFiles(true);
      try {
        const res = await groupsAPI.getMaterials(selectedGroupId);
        setFiles(res.data.results || res.data || []);
      } catch (err) {
        console.error("Failed to load files", err);
      } finally {
        setLoadingFiles(false);
      }
    };
    fetchFiles();
  }, [selectedGroupId]);

  // ── Upload handler ───────────────────────────────────────────
  const handleUpload = async () => {
    if (!selectedFile || !selectedGroupId || !uploadTitle.trim()) {
      alert("Please select a group, enter a title, and choose a file.");
      return;
    }

    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('title', uploadTitle);
      formData.append('file', selectedFile);
      formData.append('study_group', selectedGroupId);

      await api.post('/materials/', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      if (isImage(selectedFile.name)) {
        try {
          const vsFormData = new FormData();
          vsFormData.append('image', selectedFile);
          vsFormData.append('group_id', selectedGroupId);
          vsFormData.append('title', uploadTitle);
          await api.post('/visual-search/upload/', vsFormData, {
            headers: { 'Content-Type': 'multipart/form-data' },
          });
          console.log('✅ Image indexed for visual search (1280-dim MobileNetV2 vector stored)');
        } catch (vsErr) {
          console.warn('Visual search indexing failed (non-critical):', vsErr);
        }
      }

      const res = await groupsAPI.getMaterials(selectedGroupId);
      setFiles(res.data.results || res.data || []);

      setUploadTitle("");
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';

    } catch (err) {
      console.error("Upload failed", err);
      alert("Upload failed. Please try again.");
    } finally {
      setUploading(false);
    }
  };

  // ── Delete handler ───────────────────────────────────────────
  const handleDelete = async (materialId: number) => {
    if (!confirm("Delete this file?")) return;
    try {
      await api.delete(`/materials/${materialId}/`);
      setFiles((prev) => prev.filter((f) => f.id !== materialId));
    } catch (err) {
      alert("Failed to delete file.");
    }
  };

  const filteredFiles = files.filter((f) =>
    f.title.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // Shared tokens
  const glassCard =
    "relative overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-2xl " +
    "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]";

  const sleekInput =
    "h-11 bg-white/[0.03] backdrop-blur border-white/[0.08] text-white " +
    "placeholder:text-slate-500 focus-visible:ring-1 focus-visible:ring-indigo-400/60 " +
    "focus-visible:border-indigo-400/50 rounded-xl transition-all";

  // ── Render ───────────────────────────────────────────────────
  return (
    <div className="relative space-y-8 p-6 md:p-10 max-w-7xl mx-auto font-sans animate-in fade-in duration-500">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <div className="inline-flex items-center gap-2 rounded-full border border-indigo-400/25 bg-indigo-500/[0.08] backdrop-blur px-3 py-1.5 text-[11px] font-medium text-indigo-200 shadow-[0_0_15px_rgba(99,102,241,0.15)] mb-3">
            <FolderOpen className="h-3 w-3 text-indigo-300" />
            Study Materials
          </div>
          <h1 className="text-3xl md:text-4xl font-semibold tracking-tight text-white">
            File{" "}
            <span className="bg-gradient-to-r from-indigo-300 via-indigo-400 to-blue-400 bg-clip-text text-transparent">
              Library
            </span>
          </h1>
          <p className="text-slate-400 mt-2 text-sm md:text-base">
            Manage and search your study materials
          </p>
        </div>

        <div className="flex gap-3">
          <Button
            onClick={() => setShowVisualSearch(!showVisualSearch)}
            className={`relative flex items-center gap-2 h-11 px-5 rounded-xl font-medium border transition-all ${
              showVisualSearch
                ? "bg-gradient-to-r from-indigo-500 to-indigo-600 text-white border-indigo-400/30 shadow-[0_0_25px_rgba(99,102,241,0.6)]"
                : "bg-white/[0.03] backdrop-blur text-indigo-200 border-indigo-400/30 hover:bg-indigo-500/10 hover:border-indigo-400/50 shadow-[0_0_18px_rgba(99,102,241,0.25)] hover:shadow-[0_0_28px_rgba(99,102,241,0.5)]"
            }`}
          >
            {showVisualSearch && (
              <span className="absolute inset-0 rounded-xl bg-indigo-400/30 blur-md animate-pulse" />
            )}
            <span className="relative flex items-center gap-2">
              <Sparkles className="w-4 h-4" />
              <ImageIcon className="w-4 h-4" />
              {showVisualSearch ? "Close AI Vision" : "AI Visual Search"}
            </span>
          </Button>
        </div>
      </div>

      {/* Visual Search Panel */}
      {showVisualSearch && (
        <div className={`animate-in fade-in slide-in-from-bottom-4 duration-300 ${glassCard} p-1`}>
          <VisualSearch groupId={selectedGroupId || null} />
        </div>
      )}

      {/* Group Selector */}
      <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center">
        <Select onValueChange={setSelectedGroupId} value={selectedGroupId}>
          <SelectTrigger className="w-[260px] h-11 bg-white/[0.03] backdrop-blur border-white/[0.08] text-white hover:border-indigo-400/50 hover:bg-white/[0.05] hover:shadow-[0_0_15px_rgba(99,102,241,0.25)] focus:ring-1 focus:ring-indigo-400/60 rounded-xl transition-all">
            <SelectValue placeholder="Select a Study Group" />
          </SelectTrigger>
          <SelectContent className="bg-[#0a0f1e]/95 backdrop-blur-2xl border-white/[0.08] shadow-[0_0_30px_rgba(99,102,241,0.25)]">
            {groups.map((g) => (
              <SelectItem
                key={g.id}
                value={String(g.id)}
                className="text-slate-300 focus:bg-indigo-500/10 focus:text-indigo-200"
              >
                {g.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {selectedGroupId && (
          <div className="inline-flex items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.03] backdrop-blur px-3 py-1.5 text-xs text-slate-400">
            <span className="tabular-nums text-white font-medium">{files.length}</span>
            <span>file{files.length !== 1 ? 's' : ''} in this group</span>
          </div>
        )}
      </div>

      {/* Upload Section — premium */}
      {selectedGroupId && (
        <Card className={`${glassCard} p-0`}>
          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/70 to-transparent" />
          <div className="pointer-events-none absolute -top-24 -right-24 h-48 w-48 rounded-full bg-indigo-500/20 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-24 -left-24 h-40 w-40 rounded-full bg-blue-500/15 blur-3xl" />

          <CardContent className="relative p-6">
            <div className="flex items-center gap-3 mb-5">
              <div className="relative">
                <div className="absolute inset-0 rounded-xl bg-indigo-500/30 blur-lg" />
                <div className="relative h-10 w-10 rounded-xl border border-indigo-400/30 bg-gradient-to-br from-indigo-500/25 to-indigo-700/15 backdrop-blur flex items-center justify-center shadow-[0_0_18px_rgba(99,102,241,0.45)]">
                  <Upload className="w-5 h-5 text-indigo-200 drop-shadow-[0_0_6px_rgba(99,102,241,0.7)]" />
                </div>
              </div>
              <div>
                <h2 className="font-semibold text-white text-base tracking-tight">Upload New File</h2>
                <p className="text-xs text-slate-400 mt-0.5">Add materials to your study group</p>
              </div>
            </div>

            <div className="flex flex-col sm:flex-row gap-3">
              <Input
                placeholder="File title (e.g. Chapter 3 Notes)"
                value={uploadTitle}
                onChange={(e) => setUploadTitle(e.target.value)}
                className={`${sleekInput} flex-1`}
              />
              <label className="group flex items-center gap-2 h-11 px-4 border border-white/[0.08] bg-white/[0.03] backdrop-blur rounded-xl cursor-pointer hover:border-indigo-400/40 hover:bg-white/[0.05] hover:shadow-[0_0_15px_rgba(99,102,241,0.2)] transition-all text-sm text-slate-300 hover:text-white shrink-0">
                <File className="w-4 h-4 text-indigo-300 group-hover:drop-shadow-[0_0_4px_rgba(99,102,241,0.6)]" />
                <span className="truncate max-w-[160px]">
                  {selectedFile ? selectedFile.name : 'Choose File'}
                </span>
                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  accept=".pdf,.jpg,.jpeg,.png,.py,.js,.ts,.java,.txt"
                  onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                />
              </label>
              <Button
                onClick={handleUpload}
                disabled={uploading || !selectedFile || !uploadTitle.trim()}
                className="relative h-11 px-6 rounded-xl bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 text-white border border-indigo-400/30 shadow-[0_0_20px_rgba(99,102,241,0.55)] hover:shadow-[0_0_30px_rgba(99,102,241,0.75)] disabled:opacity-40 disabled:shadow-none transition-all font-medium shrink-0"
              >
                {uploading
                  ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> Uploading…</>
                  : <><Upload className="w-4 h-4 mr-2" /> Upload</>
                }
              </Button>
            </div>

            {selectedFile && isImage(selectedFile.name) && (
              <div className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-indigo-400/25 bg-indigo-500/[0.08] backdrop-blur px-3 py-1 text-[11px] font-medium text-indigo-200 shadow-[0_0_12px_rgba(99,102,241,0.2)]">
                <Sparkles className="w-3 h-3 text-indigo-300" />
                <ImageIcon className="w-3 h-3" />
                This image will be auto-indexed for AI Visual Search
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* File List */}
      <div className="space-y-4">
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="text-xl font-semibold text-white tracking-tight flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-indigo-400 shadow-[0_0_8px_rgba(99,102,241,1)]" />
            {selectedGroupId ? 'Files in Group' : 'My Files'}
          </h2>
          {selectedGroupId && (
            <div className="relative flex-1 max-w-sm">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500" />
              <Input
                placeholder="Search files…"
                className={`pl-10 h-9 ${sleekInput}`}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
          )}
        </div>

        {!selectedGroupId ? (
          <div className="text-center py-20 border-2 border-dashed border-white/10 rounded-2xl bg-white/[0.02] backdrop-blur">
            <div className="h-16 w-16 mx-auto rounded-2xl border border-white/[0.08] bg-white/[0.03] flex items-center justify-center mb-4">
              <FolderOpen className="h-7 w-7 text-slate-500" />
            </div>
            <p className="text-slate-400 font-medium">Select a study group to see its files</p>
          </div>
        ) : loadingFiles ? (
          <div className="flex justify-center py-20">
            <div className="relative">
              <div className="h-12 w-12 rounded-full border-2 border-white/10" />
              <div className="absolute inset-0 h-12 w-12 rounded-full border-2 border-transparent border-t-indigo-400 animate-spin shadow-[0_0_25px_rgba(99,102,241,0.5)]" />
            </div>
          </div>
        ) : filteredFiles.length === 0 ? (
          <div className="text-center py-20 border-2 border-dashed border-white/10 rounded-2xl bg-white/[0.02] backdrop-blur">
            <div className="h-16 w-16 mx-auto rounded-2xl border border-white/[0.08] bg-white/[0.03] flex items-center justify-center mb-4">
              <FileText className="h-7 w-7 text-slate-500" />
            </div>
            <p className="text-slate-400 font-medium">
              {searchQuery ? 'No files match your search.' : 'No files uploaded yet.'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredFiles.map((file, idx) => (
              <div
                key={file.id}
                className="group relative animate-in fade-in slide-in-from-bottom-2 duration-500"
                style={{ animationDelay: `${idx * 40}ms` }}
              >
                {/* Aurora hover glow */}
                <div className="absolute -inset-0.5 rounded-2xl bg-gradient-to-br from-indigo-500/0 via-indigo-500/0 to-blue-500/0 group-hover:from-indigo-500/30 group-hover:to-blue-500/30 blur-lg opacity-0 group-hover:opacity-100 transition-opacity duration-500" />

                <Card
                  className={`relative ${glassCard} transition-all duration-300
                    group-hover:border-indigo-400/40
                    group-hover:-translate-y-1
                    group-hover:shadow-[0_15px_40px_-15px_rgba(99,102,241,0.5)]`}
                >
                  <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-indigo-400/0 to-transparent group-hover:via-indigo-400/70 transition-all duration-500" />

                  <CardContent className="relative p-4">
                    <div className="flex items-start gap-3">
                      <div className="h-11 w-11 rounded-xl border border-white/[0.08] bg-white/[0.03] backdrop-blur flex items-center justify-center shrink-0 group-hover:border-indigo-400/40 group-hover:bg-indigo-500/10 group-hover:shadow-[0_0_15px_rgba(99,102,241,0.35)] transition-all">
                        {fileIcon(file.file)}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="font-medium text-sm text-white truncate group-hover:text-indigo-200 transition-colors" title={file.title}>
                          {file.title}
                        </p>
                        <p className="text-[11px] text-slate-500 mt-0.5 font-mono">
                          {file.uploaded_by?.username} · {formatDate(file.upload_date)}
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center gap-2 mt-4">
                      <a
                        href={`${import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'}${file.file}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex-1"
                      >
                        <Button
                          variant="outline"
                          size="sm"
                          className="w-full h-8 text-xs rounded-lg border-white/[0.08] bg-white/[0.03] text-slate-300 hover:bg-indigo-500/10 hover:text-indigo-200 hover:border-indigo-400/40 transition-all"
                        >
                          <Eye className="w-3 h-3 mr-1" /> View
                        </Button>
                      </a>
                      <a
                        href={`${import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'}${file.file}`}
                        download
                      >
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-8 w-8 p-0 rounded-lg border-white/[0.08] bg-white/[0.03] text-slate-300 hover:bg-indigo-500/10 hover:text-indigo-200 hover:border-indigo-400/40 transition-all"
                        >
                          <Download className="w-3 h-3" />
                        </Button>
                      </a>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8 w-8 p-0 rounded-lg text-slate-400 hover:text-rose-300 hover:bg-rose-500/10 border border-transparent hover:border-rose-400/30 transition-all"
                        onClick={() => handleDelete(file.id)}
                      >
                        <Trash2 className="w-3 h-3" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}