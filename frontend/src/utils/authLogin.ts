export const DEFAULT_AUTH_LOGIN_URL =
  "https://filenkov.store/oauth/google?return_to=https://storage.filenkov.store/";

const PROTECTED_PATH_PREFIXES = ["/files", "/photos", "/private", "/shared", "/admin"];

let hubRedirectScheduled = false;

export function getAuthLoginUrl(): string {
  const configured = import.meta.env.VITE_AUTH_LOGIN_URL;
  if (typeof configured === "string" && configured.trim() !== "") {
    return configured.trim();
  }
  return DEFAULT_AUTH_LOGIN_URL;
}

export function isProtectedAppPath(pathname: string): boolean {
  return PROTECTED_PATH_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function shouldRedirectToHubOnUnauthorized(
  status: number,
  errorCode: string | undefined,
  requestUrl: string | undefined,
  pathname: string,
): boolean {
  if (status === 503 || status !== 401) {
    return false;
  }
  if (errorCode === "PRIVATE_SESSION_EXPIRED") {
    return false;
  }
  if (requestUrl?.includes("/api/auth/me")) {
    return false;
  }
  return isProtectedAppPath(pathname);
}

export function redirectToAuthLogin(): void {
  if (hubRedirectScheduled) {
    return;
  }
  hubRedirectScheduled = true;
  window.location.assign(getAuthLoginUrl());
}
