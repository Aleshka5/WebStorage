import { create } from "zustand";
import api from "../services/api";

export interface Quota {
  used_bytes: number;
  limit_bytes: number;
  private_bytes: number;
  private_limit_bytes: number;
}

interface QuotaState {
  quota: Quota | null;
  isLoading: boolean;
  fetchQuota: () => Promise<void>;
}

export const useQuotaStore = create<QuotaState>((set) => ({
  quota: null,
  isLoading: false,

  fetchQuota: async () => {
    set({ isLoading: true });

    try {
      const { data } = await api.get<Quota>("/api/quota/me");
      set({ quota: data, isLoading: false });
    } catch {
      set({ quota: null, isLoading: false });
    }
  },
}));
