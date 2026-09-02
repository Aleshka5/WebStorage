import { isAxiosError } from "axios";
import { create } from "zustand";
import api, { getApiErrorDetail } from "../services/api";

export interface User {
  user_id: string;
  email: string;
  role: string;
}

interface AuthState {
  user: User | null;
  isLoading: boolean;
  userServiceUnavailable: boolean;
  unauthorized: boolean;
  fetchMe: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isLoading: false,
  userServiceUnavailable: false,
  unauthorized: false,

  fetchMe: async () => {
    try {
      const { data } = await api.get<User>("/api/auth/me");
      set({
        user: data,
        userServiceUnavailable: false,
        unauthorized: false,
      });
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 401) {
        set({ user: null, userServiceUnavailable: false, unauthorized: true });
        return;
      }

      const code = getApiErrorDetail(error)?.error_code;
      set({
        user: null,
        unauthorized: false,
        userServiceUnavailable:
          isAxiosError(error) &&
          (error.response?.status === 503 || code === "USER_SERVICE_UNAVAILABLE"),
      });
    }
  },
}));
