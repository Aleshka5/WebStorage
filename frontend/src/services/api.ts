import axios, { isAxiosError } from "axios";

export const PRIVATE_SESSION_EXPIRED_EVENT = "homecloud:private-session-expired";

export interface ApiErrorDetail {
  error_code?: string;
  message?: string;
}

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "",
  withCredentials: true,
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (isAxiosError(error) && error.response?.status === 401) {
      const detail = error.response.data?.detail;

      if (
        detail &&
        typeof detail === "object" &&
        detail.error_code === "PRIVATE_SESSION_EXPIRED"
      ) {
        window.dispatchEvent(new CustomEvent(PRIVATE_SESSION_EXPIRED_EVENT));
      }
    }

    return Promise.reject(error);
  },
);

export function getApiErrorDetail(error: unknown): ApiErrorDetail | null {
  if (!isAxiosError(error)) {
    return null;
  }

  const detail = error.response?.data?.detail;

  if (detail && typeof detail === "object") {
    return detail as ApiErrorDetail;
  }

  return null;
}

export default api;
