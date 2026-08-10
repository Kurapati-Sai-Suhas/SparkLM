import axios from "axios";

const API_BASE_URL = `${import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'}/api`;

// 1. CREATE THE AXIOS INSTANCE (The Engine)
const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    "Content-Type": "application/json",
    // Auth v2 (M5 Phase 4): proves the request came from our own XHR
    // rather than a cross-site form or image. The refresh cookie is
    // SameSite=None (Vercel and Render are different sites), so the
    // browser would otherwise attach it to a forged cross-site POST.
    // A custom header forces a CORS preflight our origin allowlist
    // answers for. Harmless when the backend flag is off.
    "X-SparkLM-Client": "web",
  },
  // Required for the httpOnly refresh cookie to be sent at all.
  withCredentials: true,
});

// ── Access token: in memory, not localStorage (Auth v2) ────────────────
//
// A module-scoped variable dies with the tab. localStorage does not, and
// is readable by any script that gets injected — which is the entire
// reason this phase exists. The refresh token is no longer here at all;
// it lives in an httpOnly cookie the browser attaches automatically.
//
// Nothing seeds this from storage. On a fresh page load it is null and
// bootstrapAuth() below exchanges the httpOnly refresh cookie for a new
// access token — which is the only way an in-memory token can survive a
// reload without also being readable by script.
let accessToken = null;

export function setAccessToken(token) {
  accessToken = token || null;
  // The localStorage mirror is GONE (M5 Phase 4 follow-up). It was the
  // last script-readable credential, kept only because 13 components read
  // the token directly; they now call getAccessToken(). One stale key is
  // cleared on the way past so a browser carrying a token from before
  // this deploy does not keep it indefinitely.
  localStorage.removeItem('authToken');
}

export function getAccessToken() {
  return accessToken;
}

// Exchange the refresh cookie for an access token exactly once per page
// load. Resolves true when a session was recovered.
//
// Required by in-memory tokens, not a convenience: ProtectedRoute used to
// answer "are we signed in?" by reading localStorage synchronously. With
// nothing in storage that check would redirect every reload to /auth even
// though the cookie is perfectly valid. The question is now answered by
// the server, which is the only party that can answer it.
let bootstrapPromise = null;

export function bootstrapAuth() {
  if (!bootstrapPromise) {
    const legacyRefresh = localStorage.getItem('refreshToken');
    bootstrapPromise = api
      .post('/token/refresh/', legacyRefresh ? { refresh: legacyRefresh } : {})
      .then(({ data }) => {
        setAccessToken(data.access);
        // Legacy path may still return a rotated body token; keeping it is
        // what lets a rollback to AUTH_V2_COOKIES=false keep working.
        if (data.refresh) localStorage.setItem('refreshToken', data.refresh);
        return true;
      })
      .catch(() => false);
  }
  return bootstrapPromise;
}

// Test/logout seam: forget the in-flight bootstrap so a later sign-in
// starts clean rather than resolving against the previous session.
export function resetAuthBootstrap() {
  bootstrapPromise = null;
}

// 2. REQUEST INTERCEPTOR (Attaches Token Automatically)
api.interceptors.request.use(
  (config) => {
    if (accessToken) {
      config.headers.Authorization = `Bearer ${accessToken}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// 3. RESPONSE INTERCEPTOR: on a 401, try the refresh token ONCE and retry
// the original request. Only if refresh also fails do we clear storage
// and redirect — previously a plain 401 wiped the tokens and left the
// page showing zeroed/empty state with no redirect, which is exactly
// what silently expiring (the access token lives 60 minutes) looked
// like to a user: "it shows dummy data until I log out and back in".
let refreshPromise = null;

function redirectToLogin() {
  setAccessToken(null);
  resetAuthBootstrap();
  localStorage.removeItem('refreshToken');
  if (window.location.pathname !== '/auth') {
    window.location.href = '/auth';
  }
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    const isAuthEndpoint = original?.url?.includes('/token/');

    if (error.response?.status === 401 && original && !original._retried && !isAuthEndpoint) {
      original._retried = true;
      // Under Auth v2 the refresh token is an httpOnly cookie the browser
      // attaches on its own, so there is nothing to look up. The legacy
      // body token is still sent when one exists, which is what keeps a
      // pre-rollout session working until it next rotates.
      const legacyRefresh = localStorage.getItem('refreshToken');

      try {
        // Single shared refresh in flight even if several requests 401 at
        // once. This is now load-bearing rather than a nicety: rotation
        // makes each refresh token single-use, so two parallel refreshes
        // would race and one would be rejected as a replay.
        if (!refreshPromise) {
          refreshPromise = api
            .post('/token/refresh/', legacyRefresh ? { refresh: legacyRefresh } : {})
            .finally(() => { refreshPromise = null; });
        }
        const { data } = await refreshPromise;
        setAccessToken(data.access);
        // The rotated refresh token arrives as a Set-Cookie header, not in
        // the body. If the backend is still on the legacy path it may
        // return one; store it so a rollback keeps working.
        if (data.refresh) localStorage.setItem('refreshToken', data.refresh);
        original.headers.Authorization = `Bearer ${data.access}`;
        return api(original);
      } catch (refreshError) {
        redirectToLogin();
        return Promise.reject(refreshError);
      }
    }

    return Promise.reject(error);
  }
);

// ==================== User API ====================
//
// Every path here is relative to the instance baseURL, which already ends
// in `/api` — so it is `/notifications/`, never `/api/notifications/`.
// Repeating the prefix produces `/api/api/...` and a 404.
//
// N3 Phase 1a, Task 1. Five methods were removed because they addressed
// routes the backend does not serve; each had ZERO call sites, so removing
// them changes no behaviour:
//
//   getAchievements       GET   /user/achievements/        no such route
//   getStats              GET   /user/stats/               no such route
//   getNotifications      GET   /user/notifications/       no such route
//   markNotificationRead  PATCH /user/notifications/{id}/read/  no such route
//   updateProfile         PATCH /user/profile/             route exists, but
//                                                          UserProfileView
//                                                          implements only
//                                                          get() -> 405
//
// The five below replace them with paths that exist in groups/urls.py.
// They are the client surface Tasks 2-4 migrate the three broken pages
// onto; nothing calls them yet, which is why this task is behaviour-neutral.
export const userAPI = {
  getDashboardBootstrap: () => api.get('/dashboard/bootstrap/'),
  getDashboardStats: () => api.get('/dashboard/stats/'),
  getProfile: () => api.get('/user/profile/'),

  // Settings page (/api/settings/...). Kept on userAPI rather than a new
  // export: both endpoints are scoped to the current user, and the page
  // reads them as one profile object.
  getSettings: () => api.get('/settings/profile/'),
  updateSettings: (settings) => api.put('/settings/profile/', settings),
  sendTestEmail: () => api.post('/settings/email/'),

  // Notifications page. The backend exposes a collection-level PUT that
  // marks every unread notification read — there is no per-id endpoint,
  // which is what the removed markNotificationRead wrongly assumed.
  getNotifications: () => api.get('/notifications/'),
  markAllNotificationsRead: () => api.put('/notifications/'),
};

// ==================== Authentication API ====================
export const authAPI = {
  login: async (username, password) => {
    const response = await api.post('/token/', { username, password });
    if (response.data.access) {
      setAccessToken(response.data.access);
      // Present only while the backend flag is off. Under Auth v2 the
      // refresh token arrives as an httpOnly cookie and is absent here,
      // which is the point — nothing readable is left behind.
      if (response.data.refresh) localStorage.setItem('refreshToken', response.data.refresh);
      else localStorage.removeItem('refreshToken');
    }
    return response.data;
  },
  signup: (username, email, password) => api.post('/register/', { username, email, password }),
  googleLogin: async (credential) => {
    const response = await api.post('/auth/google/', { credential });
    if (response.data.access) {
      setAccessToken(response.data.access);
      if (response.data.refresh) localStorage.setItem('refreshToken', response.data.refresh);
      else localStorage.removeItem('refreshToken');
    }
    return response.data;
  },
  logout: async () => {
    // Must reach the server. An httpOnly cookie cannot be removed by
    // client script, so clearing localStorage alone would leave a live
    // refresh token in the browser — a logout that logs nobody out.
    try {
      await api.post('/auth/logout/', {});
    } catch {
      // Network failure or already signed out. Clearing locally is still
      // correct; the cookie expires on its own and rotation limits reuse.
    }
    setAccessToken(null);
    resetAuthBootstrap();
    localStorage.removeItem('refreshToken');
    window.location.href = "/auth";
  },
};

// ==================== Study Groups API ====================
export const groupsAPI = {
  getAll: () => api.get('/groups/'),
  
  create: (groupData) => api.post('/groups/', groupData),
  
  getById: (groupId) => api.get(`/groups/${groupId}/`),
  
  join: (code) => api.post('/groups/join/', { code }),
  
  leave: (groupId) => api.delete(`/groups/${groupId}/leave/`),
  
  update: (groupId, updates) => api.patch(`/groups/${groupId}/`, updates),

  // 📦 FILE UPLOAD (FIXED)
  uploadMaterial: (title, file, groupId) => {
    const formData = new FormData();
    formData.append('title', title);
    formData.append('study_group', groupId); // Matches backend field name
    formData.append('file', file);

    // 👇 THE FIX: We MUST override the default JSON header here.
    return api.post('/materials/', formData, {
      headers: {
        "Content-Type": "multipart/form-data",
      },
    });
  },

  // We use 'study_group' to filter by ID exactly, bypassing the fuzzy text search
   getMaterials: (groupId) => api.get(`/materials/?study_group=${groupId}`),

  // Authorized download (M5 Phase 3). Two steps on purpose: this request
  // carries the bearer token and the backend re-checks membership before
  // minting a short-lived signed URL. The file itself is then fetched
  // straight from object storage, so the bytes never transit the 0.1 vCPU
  // instance. Returns { url, expires_in }; 404 if the caller may not read
  // the material, 410 if the row outlived its file.
  getDownloadUrl: (materialId) => api.get(`/materials/${materialId}/download/`),
};

// ==================== AI Features API ====================
export const aiAPI = {

  generateQuiz: (materialId, topic, questionCount = 10, difficulty = 'medium') => 
    api.post('/ai/quiz/', { materialId, topic, questionCount, difficulty }),

  // submitQuiz removed (Task 1): posted to /ai/quiz/{id}/submit/, which the
  // backend does not serve. Quiz results are saved via /quiz/save/ and
  // assigned quizzes managed under /quizzes/assigned/{pk}/. Zero call sites.

  askDoubt: (question, context = null, attachments = []) =>
    api.post('/ai/doubt/', { question, context, attachments }),
};

// ==================== Coding API ====================
export const codeAPI = {
  /**
   * Execute the learner's current source against the question's PUBLIC
   * sample input (M1/P1.2-A "Run before Submit").
   *
   * `problemId` is required, not optional. The backend builds what actually
   * executes from the question — per-question wrapper first, generic harness
   * otherwise — so omitting the id makes a `class Solution` stub run as bare
   * source that defines a class, calls nothing and prints nothing. The old
   * portal omitted it, which is why its Run button looked like it worked.
   *
   * Judged output is NOT returned here: this runs one public case and never
   * touches Elo, mastery or the hidden tests. Use codeAPI.submit for that.
   */
  runCode: ({ code, language, problemId, stdin = '' }) =>
    api.post('/code/run/', {
      code,
      language,
      problem_id: problemId,
      stdin,
    }),
};

// ==================== Schedule API ====================
export const scheduleAPI = {
  // Correct and already matching the backend — Task 2 migrates Schedule.tsx
  // onto these rather than adding anything new.
  getSchedule: () => api.get('/schedule/'),
  createEvent: (eventData) => api.post('/schedule/', eventData),
  // updateEvent / deleteEvent removed (Task 1): both addressed
  // /schedule/{id}/, which the backend does not serve — ScheduleView exposes
  // only collection-level GET and POST. Zero call sites.
};

export default api;