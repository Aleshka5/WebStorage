import api from "./api";

export interface PrivateSession {
  active: boolean;
  expires_in_seconds: number;
}

export interface PrivateQuota {
  private_bytes: number;
  private_limit_bytes: number;
}

export async function getPrivateSession(): Promise<PrivateSession> {
  const { data } = await api.get<PrivateSession>("/api/private/session");
  return data;
}

export async function unlockPrivate(passphrase: string): Promise<{ success: boolean }> {
  const { data } = await api.post<{ success: boolean }>("/api/private/unlock", {
    passphrase,
  });
  return data;
}

export async function getPrivateQuota(): Promise<PrivateQuota> {
  const { data } = await api.get<PrivateQuota>("/api/private/quota");
  return data;
}

export async function resetPrivateStorage(): Promise<void> {
  await api.post("/api/private/reset");
}
