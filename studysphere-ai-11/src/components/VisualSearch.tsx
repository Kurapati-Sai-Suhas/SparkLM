import React, { useState } from 'react';
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { UploadCloud, Search, Loader2, Image as ImageIcon, X } from "lucide-react";
import api from "@/services/api"; // ✅ Use your axios instance, not raw fetch

interface VisualSearchProps {
  groupId?: string | null;
}

interface SearchResult {
  document_id: number;
  title: string;
  similarity_score: number;
  uploaded_by: string;
}
// No file_url: MEDIA is served unauthenticated, so the backend no longer
// returns a direct link to the image bytes. Results identify the match by
// document_id and title; restoring thumbnails needs an authenticated
// image route, not a URL in this payload.

export default function VisualSearch({ groupId }: VisualSearchProps) {
  const [selectedImage, setSelectedImage] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [searched, setSearched] = useState(false);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setSelectedImage(file);
      setPreviewUrl(URL.createObjectURL(file));
      setResults([]);
      setError('');
      setSearched(false);
    }
  };

  const clearImage = () => {
    setSelectedImage(null);
    setPreviewUrl(null);
    setResults([]);
    setError('');
    setSearched(false);
  };

  const handleSearch = async () => {
    if (!selectedImage) return;

    setLoading(true);
    setError('');

    const formData = new FormData();
    formData.append('image', selectedImage);
    if (groupId) formData.append('group_id', groupId);
    formData.append('top_k', '5');

    try {
      // ✅ Uses your axios instance (handles baseURL + auth token automatically)
      const response = await api.post('/visual-search/query/', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      setResults(response.data.query_results || []);
      setSearched(true);

      if ((response.data.query_results || []).length === 0) {
        setError('No similar diagrams found. Try uploading more images to this group first.');
      }
    } catch (err: any) {
      const msg = err?.response?.data?.error || 'Search failed. Is your backend running?';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  // Score color: green > 80%, yellow > 50%, red below
  const scoreColor = (score: number) => {
    if (score >= 0.8) return 'bg-green-500';
    if (score >= 0.5) return 'bg-yellow-500';
    return 'bg-red-400';
  };

  return (
    <Card className="border-border shadow-md bg-white">
      <div className="border-b bg-gradient-to-r from-blue-600 to-indigo-600 p-6 rounded-t-xl">
        <h3 className="text-xl font-bold text-white flex items-center gap-2">
          <ImageIcon className="w-5 h-5" />
          AI Visual Semantic Search
        </h3>
        <p className="text-blue-100 text-sm mt-1">
          Upload a cropped diagram or screenshot to find where it appears in your study materials.
        </p>
        {!groupId && (
          <p className="text-yellow-200 text-xs mt-2 bg-white/10 px-3 py-1 rounded-lg inline-block">
            ⚠️ No group selected — searching across all your materials
          </p>
        )}
      </div>

      <CardContent className="p-6">
        <div className="flex flex-col md:flex-row gap-6">

          {/* Left: Upload + Search */}
          <div className="flex-1 flex flex-col gap-4">
            <div className="relative border-2 border-dashed border-slate-300 rounded-xl p-8 flex flex-col items-center justify-center bg-slate-50 hover:bg-slate-100 transition-colors cursor-pointer group min-h-[200px]">
              <input
                type="file"
                accept="image/*"
                onChange={handleFileChange}
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-10"
              />
              {previewUrl ? (
                <div className="relative w-full flex justify-center">
                  <img
                    src={previewUrl}
                    alt="Query Preview"
                    className="h-48 object-contain rounded-md shadow-sm"
                  />
                  {/* Clear button — sits above the file input so needs z-20 */}
                  <button
                    onClick={(e) => { e.stopPropagation(); clearImage(); }}
                    className="absolute top-0 right-0 z-20 bg-red-500 hover:bg-red-600 text-white rounded-full p-1 shadow"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </div>
              ) : (
                <div className="text-center flex flex-col items-center pointer-events-none">
                  <div className="h-12 w-12 rounded-full bg-blue-100 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform">
                    <UploadCloud className="h-6 w-6 text-blue-600" />
                  </div>
                  <p className="text-sm font-medium text-slate-700">Click or drag image to upload</p>
                  <p className="text-xs text-slate-500 mt-1">Supports JPG, PNG</p>
                </div>
              )}
            </div>

            <Button
              onClick={handleSearch}
              disabled={!selectedImage || loading}
              className="w-full h-11 bg-blue-600 hover:bg-blue-700 text-white font-medium text-md shadow-sm"
            >
              {loading ? (
                <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> Running MobileNetV2...</>
              ) : (
                <><Search className="w-4 h-4 mr-2" /> Search Vectors</>
              )}
            </Button>

            {error && (
              <p className="text-red-500 text-sm font-medium text-center bg-red-50 p-2 rounded-md border border-red-100">
                {error}
              </p>
            )}
          </div>

          {/* Right: Results */}
          <div className="flex-[1.5] bg-slate-50 rounded-xl p-4 border border-slate-200 min-h-[300px]">
            <h4 className="font-semibold text-slate-800 mb-4 flex items-center justify-between">
              Matches Found
              <span className="text-xs font-normal bg-white px-2 py-1 rounded-md border border-slate-200 text-slate-500">
                Sorted by Cosine Similarity
              </span>
            </h4>

            {results.length > 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {results.map((doc) => (
                  <div
                    key={doc.document_id}
                    className="bg-white border border-slate-200 rounded-lg p-3 hover:shadow-md transition-all group"
                  >
                    <div className="aspect-video bg-slate-100 rounded-md mb-3 overflow-hidden relative border border-slate-100 flex items-center justify-center">
                      <ImageIcon className="w-10 h-10 text-slate-300" />
                      <div className={`absolute top-2 right-2 ${scoreColor(doc.similarity_score)} text-white text-xs font-bold px-2 py-1 rounded shadow-sm`}>
                        {(doc.similarity_score * 100).toFixed(1)}% Match
                      </div>
                    </div>
                    <p className="font-semibold text-sm text-slate-800 truncate" title={doc.title}>
                      {doc.title}
                    </p>
                    <p className="text-xs text-slate-500 mt-1">
                      Uploaded by <span className="font-medium text-slate-700">{doc.uploaded_by}</span>
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <div className="h-full flex flex-col items-center justify-center text-slate-400 opacity-70 mt-8">
                <Search className="w-12 h-12 mb-3" />
                <p>{searched ? 'No matches found.' : 'Awaiting image query...'}</p>
              </div>
            )}
          </div>

        </div>
      </CardContent>
    </Card>
  );
}