import axios from "axios";

const API_BASE_URL = `${import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'}/api`;

// 1. CREATE THE AXIOS INSTANCE (The Engine)
const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    "Content-Type": "application/json",
  },
});

// 2. REQUEST INTERCEPTOR (Attaches Token Automatically)
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('authToken');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
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
  localStorage.removeItem('authToken');
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
      const refreshToken = localStorage.getItem('refreshToken');
      if (!refreshToken) {
        redirectToLogin();
        return Promise.reject(error);
      }

      try {
        // Single shared refresh in flight even if several requests 401
        // at once — avoids hammering /token/refresh/ with duplicates.
        if (!refreshPromise) {
          refreshPromise = api
            .post('/token/refresh/', { refresh: refreshToken })
            .finally(() => { refreshPromise = null; });
        }
        const { data } = await refreshPromise;
        localStorage.setItem('authToken', data.access);
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
export const userAPI = {
  getDashboardBootstrap: () => api.get('/dashboard/bootstrap/'),
  getDashboardStats: () => api.get('/dashboard/stats/'),
  getProfile: () => api.get('/user/profile/'),
  updateProfile: (updates) => api.patch('/user/profile/', updates),
  getAchievements: () => api.get('/user/achievements/'),
  getStats: () => api.get('/user/stats/'),
  getNotifications: () => api.get('/user/notifications/'),
  markNotificationRead: (id) => api.patch(`/user/notifications/${id}/read/`),
};

// ==================== Authentication API ====================
export const authAPI = {
  login: async (username, password) => {
    const response = await api.post('/token/', { username, password });
    if (response.data.access) {
      localStorage.setItem('authToken', response.data.access);
      if (response.data.refresh) localStorage.setItem('refreshToken', response.data.refresh);
    }
    return response.data;
  },
  signup: (username, email, password) => api.post('/register/', { username, email, password }),
  googleLogin: async (credential) => {
    const response = await api.post('/auth/google/', { credential });
    if (response.data.access) {
      localStorage.setItem('authToken', response.data.access);
      if (response.data.refresh) localStorage.setItem('refreshToken', response.data.refresh);
    }
    return response.data;
  },
  logout: () => {
    localStorage.removeItem('authToken');
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
  generateFlashcards: (materialId, topic, count = 10) => 
    api.post('/ai/flashcards/', { materialId, topic, count }),

  generateQuiz: (materialId, topic, questionCount = 10, difficulty = 'medium') => 
    api.post('/ai/quiz/', { materialId, topic, questionCount, difficulty }),

  submitQuiz: (quizId, answers) => 
    api.post(`/ai/quiz/${quizId}/submit/`, { answers }),

  askDoubt: (question, context = null, attachments = []) => 
    api.post('/ai/doubt/', { question, context, attachments }),
};

// ==================== Schedule API ====================
export const scheduleAPI = {
  getSchedule: () => api.get('/schedule/'),
  createEvent: (eventData) => api.post('/schedule/', eventData),
  updateEvent: (eventId, updates) => api.patch(`/schedule/${eventId}/`, updates),
  deleteEvent: (eventId) => api.delete(`/schedule/${eventId}/`),
};

export default api;