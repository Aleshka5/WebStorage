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
  sessionChecked: boolean;
  fetchMe: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isLoading: false,
  userServiceUnavailable: false,
  unauthorized: false,
  sessionChecked: false,

  fetchMe: async () => {
    set({ sessionChecked: false });

    try {
      const { data } = await api.get<User>("/api/auth/me");
      set({
        user: data,
        userServiceUnavailable: false,
        unauthorized: false,
        sessionChecked: true,
      });
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 401) {
        set({
          user: null,
          userServiceUnavailable: false,
          unauthorized: true,
          sessionChecked: true,
        });
        return;
      }

      const code = getApiErrorDetail(error)?.error_code;
      set({
        user: null,
        unauthorized: false,
        sessionChecked: true,
        userServiceUnavailable:
          isAxiosError(error) &&
          (error.response?.status === 503 || code === "USER_SERVICE_UNAVAILABLE"),
      });
    }
  },
}));
