import axios, { isAxiosError } from "axios";
import { redirectToAuthLogin, shouldRedirectToHubOnUnauthorized } from "../utils/authLogin";

export const PRIVATE_SESSION_EXPIRED_EVENT = "homecloud:private-session-expired";
export const EVENT_PRIVATE_EXPIRED = PRIVATE_SESSION_EXPIRED_EVENT;

export interface ApiErrorDetail {
  error_code?: string;
  message?: string;
  available_bytes?: number;
  retry_after?: number;
}

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "",
  withCredentials: true,
});

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

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (!isAxiosError(error) || error.response === undefined) {
      return Promise.reject(error);
    }

    const status = error.response.status;
    const errorCode = getApiErrorDetail(error)?.error_code;

    if (status === 503) {
      return Promise.reject(error);
    }

    if (status === 401 && errorCode === "PRIVATE_SESSION_EXPIRED") {
      window.dispatchEvent(new CustomEvent(PRIVATE_SESSION_EXPIRED_EVENT));
      return Promise.reject(error);
    }

    if (
      shouldRedirectToHubOnUnauthorized(
        status,
        errorCode,
        error.config?.url,
        window.location.pathname,
      )
    ) {
      redirectToAuthLogin();
    }

    return Promise.reject(error);
  },
);

export default api;
