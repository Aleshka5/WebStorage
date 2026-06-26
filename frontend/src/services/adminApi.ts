import api from "./api";

export interface UserAdminView {
  id: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string;
  quota_used_bytes: number;
  private_limit_bytes: number;
}

export interface UserListResponse {
  items: UserAdminView[];
  total: number;
}

export interface DiskStat {
  id: string;
  mount_path: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  status: string;
}

export interface StorageStatsResponse {
  disks: DiskStat[];
}

export interface ListUsersParams {
  page?: number;
  limit?: number;
  role?: string;
  email?: string;
}

export async function listUsers(params: ListUsersParams = {}): Promise<UserListResponse> {
  const { data } = await api.get<UserListResponse>("/api/admin/users", { params });
  return data;
}

export async function updateUserRole(userId: string, role: string): Promise<void> {
  await api.patch(`/api/admin/users/${userId}/role`, { role });
}

export async function updateUserPrivateQuota(
  userId: string,
  privateLimitGb: number,
): Promise<void> {
  await api.patch(`/api/admin/users/${userId}/quota`, { private_limit_gb: privateLimitGb });
}

export async function blockUser(userId: string): Promise<void> {
  await api.post(`/api/admin/users/${userId}/block`);
}

export async function deleteUser(userId: string): Promise<void> {
  await api.delete(`/api/admin/users/${userId}`);
}

export async function getStorageStats(): Promise<StorageStatsResponse> {
  const { data } = await api.get<StorageStatsResponse>("/api/admin/storage");
  return data;
}
