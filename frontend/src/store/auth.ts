import { create } from "zustand";
import api, { getApiErrorDetail } from "../services/api";

export interface User {
  user_id: string;
  email: string;
  role: string;
}

export class AuthError extends Error {
  readonly errorCode?: string;
  readonly field?: "email" | "password";

  constructor(message: string, errorCode?: string, field?: "email" | "password") {
    super(message);
    this.name = "AuthError";
    this.errorCode = errorCode;
    this.field = field;
  }
}

interface AuthState {
  user: User | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  fetchMe: () => Promise<void>;
}

function mapAuthError(error: unknown): AuthError {
  const detail = getApiErrorDetail(error);

  if (detail?.error_code === "EMAIL_ALREADY_EXISTS") {
    return new AuthError(detail.message ?? "Email уже занят", detail.error_code, "email");
  }

  if (detail?.error_code === "INVALID_CREDENTIALS") {
    return new AuthError(
      detail.message ?? "Неверный email или пароль",
      detail.error_code,
      "password",
    );
  }

  return new AuthError(detail?.message ?? "Не удалось выполнить запрос");
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isLoading: false,

  login: async (email, password) => {
    set({ isLoading: true });

    try {
      const { data } = await api.post<User>("/api/auth/login", { email, password });
      set({ user: data, isLoading: false });
    } catch (error) {
      set({ isLoading: false });
      throw mapAuthError(error);
    }
  },

  register: async (email, password) => {
    set({ isLoading: true });

    try {
      await api.post<User>("/api/auth/register", { email, password });
      const { data } = await api.post<User>("/api/auth/login", { email, password });
      set({ user: data, isLoading: false });
    } catch (error) {
      set({ isLoading: false });
      throw mapAuthError(error);
    }
  },

  logout: async () => {
    set({ isLoading: true });

    try {
      await api.post("/api/auth/logout");
    } finally {
      set({ user: null, isLoading: false });
    }
  },

  fetchMe: async () => {
    try {
      const { data } = await api.get<User>("/api/auth/me");
      set({ user: data });
    } catch {
      set({ user: null });
    }
  },
}));
