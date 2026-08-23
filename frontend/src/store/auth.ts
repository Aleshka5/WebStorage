import { isAxiosError } from "axios";
import { create } from "zustand";
import api from "../services/api";

export interface User {
  user_id: string;
  email: string;
  role: string;
}

interface AuthState {
  user: User | null;
  isLoading: boolean;
  authUnavailable: boolean;
  logout: () => Promise<void>;
  fetchMe: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isLoading: false,
  authUnavailable: false,

  logout: async () => {
    set({ isLoading: true });

    try {
      await api.post("/api/auth/logout");
    } finally {
      set({ user: null, isLoading: false, authUnavailable: false });
    }
  },

  fetchMe: async () => {
    try {
      const { data } = await api.get<User>("/api/auth/me");
      set({ user: data, authUnavailable: false });
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 401) {
        set({ user: null, authUnavailable: false });
        return;
      }

      set({ authUnavailable: true });
    }
  },
}));
