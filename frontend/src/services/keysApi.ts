import api from "./api";

export interface RegistryKey {
  name: string;
  value: string;
}

export interface KeysListResponse {
  keys: RegistryKey[];
}

export async function listKeys(): Promise<KeysListResponse> {
  const { data } = await api.get<KeysListResponse>("/api/private/keys");
  return data;
}

export async function saveKeys(keys: RegistryKey[]): Promise<KeysListResponse> {
  const { data } = await api.put<KeysListResponse>("/api/private/keys", { keys });
  return data;
}
