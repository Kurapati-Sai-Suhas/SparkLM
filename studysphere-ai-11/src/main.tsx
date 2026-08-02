import { createRoot } from "react-dom/client";
import { loader } from "@monaco-editor/react";

// Inter, self-hosted (M4 Phase A, CSP). Replaces the <link> tags to
// fonts.googleapis.com / fonts.gstatic.com in index.html, removing two more
// third-party origins from the policy. Only the four weights the design
// system actually uses are imported — @fontsource ships one file per weight,
// so this is smaller over the wire than the Google stylesheet was.
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";

import App from "./App.tsx";
import "./index.css";

// Load Monaco from our own origin, not cdn.jsdelivr.net (M4 Phase A, CSP).
//
// Without this, @monaco-editor/react fetches the editor from jsDelivr, which
// would require `script-src https://cdn.jsdelivr.net` — making a jsDelivr
// compromise arbitrary code execution inside SparkLM. The files are copied
// into public/monaco/vs at build time by the self-host plugin in
// vite.config.ts; monaco-editor was already a direct dependency.
//
// Must run before any <Editor> mounts, which is why it is here rather than in
// a component: all three usage sites (AdaptiveCodingPortal, CodingPortal,
// LiveCollaborativeWorkspace) inherit it.
loader.config({ paths: { vs: "/monaco/vs" } });

createRoot(document.getElementById("root")!).render(<App />);
