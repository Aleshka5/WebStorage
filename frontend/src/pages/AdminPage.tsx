import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { Button } from "../components/ui/Button";
import {
  blockUser,
  deleteUser,
  getStorageStats,
  listUsers,
  updateUserPrivateQuota,
  type DiskStat,
  type UserAdminView,
} from "../services/adminApi";
import { useAuthStore } from "../store/auth";
import { formatBytes, formatDateTime } from "../utils/format";

const ROLES = ["STRANGER", "FAMILY", "ADMIN"] as const;
const PAGE_SIZE = 20;

type RoleFilter = (typeof ROLES)[number] | "ALL";

function bytesToGb(bytes: number): number {
  return Math.round((bytes / (1024 * 1024 * 1024)) * 10) / 10;
}

function formatDiskBytes(bytes: number): string {
  return formatBytes(bytes, false);
}

function diskStatusLabel(status: string): string {
  switch (status) {
    case "HEALTHY":
      return "OK";
    case "LOW_SPACE":
      return "Low space";
    case "UNAVAILABLE":
      return "Unavailable";
    default:
      return status;
  }
}

function diskStatusClass(status: string): string {
  switch (status) {
    case "HEALTHY":
      return "text-emerald-400";
    case "LOW_SPACE":
      return "text-amber-400";
    case "UNAVAILABLE":
      return "text-red-400";
    default:
      return "text-zinc-400";
  }
}

function StorageDiskCard({ disk }: { disk: DiskStat }) {
  const usedPercent =
    disk.total_bytes > 0
      ? Math.min(100, Math.round((disk.used_bytes / disk.total_bytes) * 100))
      : 0;

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h4 className="font-medium text-zinc-100">{disk.id}</h4>
          <p className="mt-0.5 text-xs text-zinc-500">{disk.mount_path}</p>
        </div>
        <span className={`text-xs font-medium ${diskStatusClass(disk.status)}`}>
          {diskStatusLabel(disk.status)}
        </span>
      </div>

      <div className="mb-2 h-2 w-full overflow-hidden rounded-full bg-zinc-800">
        <div
          className="h-full rounded-full bg-sky-500 transition-all duration-300"
          style={{ width: `${usedPercent}%` }}
        />
      </div>

      <div className="grid grid-cols-3 gap-2 text-xs text-zinc-400">
        <div>
          <span className="block text-zinc-500">Total</span>
          {formatDiskBytes(disk.total_bytes)}
        </div>
        <div>
          <span className="block text-zinc-500">Used</span>
          {formatDiskBytes(disk.used_bytes)}
        </div>
        <div>
          <span className="block text-zinc-500">Free</span>
          {formatDiskBytes(disk.free_bytes)}
        </div>
      </div>
    </div>
  );
}

interface QuotaInputProps {
  user: UserAdminView;
  onSaved: () => void;
}

function PrivateQuotaInput({ user, onSaved }: QuotaInputProps) {
  const [value, setValue] = useState(String(bytesToGb(user.private_limit_bytes)));
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    setValue(String(bytesToGb(user.private_limit_bytes)));
  }, [user.private_limit_bytes]);

  const handleBlur = async () => {
    const parsed = Number.parseFloat(value.replace(",", "."));
    if (Number.isNaN(parsed) || parsed < 0) {
      setValue(String(bytesToGb(user.private_limit_bytes)));
      return;
    }

    if (parsed === bytesToGb(user.private_limit_bytes)) {
      return;
    }

    setIsSaving(true);
    try {
      await updateUserPrivateQuota(user.id, parsed);
      onSaved();
    } catch {
      setValue(String(bytesToGb(user.private_limit_bytes)));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <input
      type="number"
      min={0}
      step={0.1}
      value={value}
      disabled={isSaving}
      onChange={(event) => setValue(event.target.value)}
      onBlur={() => void handleBlur()}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.currentTarget.blur();
        }
      }}
      className="w-20 rounded border border-zinc-700 bg-zinc-800 px-2 py-1 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-sky-500/50 disabled:opacity-50"
      aria-label={`Private quota for ${user.email}`}
    />
  );
}

export default function AdminPage() {
  const currentUser = useAuthStore((state) => state.user);
  const [users, setUsers] = useState<UserAdminView[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [roleFilter, setRoleFilter] = useState<RoleFilter>("ALL");
  const [emailSearch, setEmailSearch] = useState("");
  const [debouncedEmail, setDebouncedEmail] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [disks, setDisks] = useState<DiskStat[]>([]);
  const [storageLoading, setStorageLoading] = useState(true);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedEmail(emailSearch.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [emailSearch]);

  const fetchUsers = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const data = await listUsers({
        page,
        limit: PAGE_SIZE,
        role: roleFilter === "ALL" ? undefined : roleFilter,
        email: debouncedEmail || undefined,
      });
      setUsers(data.items);
      setTotal(data.total);
    } catch {
      setError("Failed to load users");
    } finally {
      setIsLoading(false);
    }
  }, [page, roleFilter, debouncedEmail]);

  const fetchStorage = useCallback(async () => {
    setStorageLoading(true);
    try {
      const data = await getStorageStats();
      setDisks(data.disks);
    } catch {
      setDisks([]);
    } finally {
      setStorageLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchUsers();
  }, [fetchUsers]);

  useEffect(() => {
    void fetchStorage();
  }, [fetchStorage]);

  if (currentUser?.role !== "ADMIN") {
    return <Navigate to="/files" replace />;
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const handleBlock = async (user: UserAdminView) => {
    if (!user.is_active) {
      return;
    }

    const confirmed = window.confirm(`Block user ${user.email}?`);
    if (!confirmed) {
      return;
    }

    try {
      await blockUser(user.id);
      void fetchUsers();
    } catch {
      window.alert("Failed to block user");
    }
  };

  const handleDelete = async (user: UserAdminView) => {
    const confirmed = window.confirm(
      `Delete user ${user.email}? All of their files will be permanently deleted.`,
    );
    if (!confirmed) {
      return;
    }

    try {
      await deleteUser(user.id);
      void fetchUsers();
    } catch {
      window.alert("Failed to delete user");
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-6 overflow-y-auto">
      <h2 className="text-xl font-semibold text-zinc-100">Admin panel</h2>

      <section className="flex flex-col gap-4">
        <h3 className="text-sm font-medium uppercase tracking-wide text-zinc-500">
          Users
        </h3>

        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-wrap gap-2">
            {(["ALL", ...ROLES] as const).map((role) => (
              <button
                key={role}
                type="button"
                onClick={() => {
                  setRoleFilter(role);
                  setPage(1);
                }}
                className={[
                  "rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                  roleFilter === role
                    ? "bg-sky-600/20 text-sky-400"
                    : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700 hover:text-zinc-100",
                ].join(" ")}
              >
                {role === "ALL" ? "All" : role}
              </button>
            ))}
          </div>

          <input
            type="search"
            placeholder="Search by email..."
            value={emailSearch}
            onChange={(event) => setEmailSearch(event.target.value)}
            className="min-w-[220px] flex-1 rounded-lg border border-zinc-700 bg-zinc-800/80 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
          />
        </div>

        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
            {error}
          </div>
        )}

        <div className="overflow-x-auto rounded-lg border border-zinc-800">
          <table className="min-w-full divide-y divide-zinc-800 text-sm">
            <thead className="bg-zinc-900/80">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-zinc-400">Email</th>
                <th className="px-4 py-3 text-left font-medium text-zinc-400">
                  Role
                  <span className="mt-0.5 block text-xs font-normal text-zinc-500">
                    changed in Auth-Service /admin
                  </span>
                </th>
                <th className="px-4 py-3 text-left font-medium text-zinc-400">Status</th>
                <th className="px-4 py-3 text-left font-medium text-zinc-400">Used</th>
                <th className="px-4 py-3 text-left font-medium text-zinc-400">
                  Private quota (GB)
                </th>
                <th className="px-4 py-3 text-left font-medium text-zinc-400">Registered</th>
                <th className="px-4 py-3 text-left font-medium text-zinc-400" />
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800 bg-zinc-950/50">
              {isLoading ? (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-zinc-500">
                    Loading...
                  </td>
                </tr>
              ) : users.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-zinc-500">
                    No users found
                  </td>
                </tr>
              ) : (
                users.map((user) => (
                  <tr key={user.id} className="hover:bg-zinc-900/40">
                    <td className="px-4 py-3 text-zinc-100">{user.email}</td>
                    <td className="px-4 py-3">
                      <span className="text-zinc-100">{user.role}</span>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={
                          user.is_active ? "text-emerald-400" : "text-red-400"
                        }
                      >
                        {user.is_active ? "Active" : "Blocked"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-zinc-300">
                      {formatBytes(user.quota_used_bytes, false)}
                    </td>
                    <td className="px-4 py-3">
                      <PrivateQuotaInput user={user} onSaved={() => void fetchUsers()} />
                    </td>
                    <td className="px-4 py-3 text-zinc-400">
                      {formatDateTime(user.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-2">
                        <Button
                          variant="danger"
                          className="w-auto px-3 py-1.5 text-xs"
                          disabled={!user.is_active || user.id === currentUser.user_id}
                          onClick={() => void handleBlock(user)}
                        >
                          Block
                        </Button>
                        <Button
                          variant="secondary"
                          className="w-auto px-3 py-1.5 text-xs"
                          disabled={user.id === currentUser.user_id}
                          onClick={() => void handleDelete(user)}
                        >
                          Delete
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {totalPages > 1 && (
          <div className="flex items-center justify-between text-sm text-zinc-400">
            <span>
              Showing {users.length} of {total}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((current) => current - 1)}
                className="rounded-lg bg-zinc-800 px-3 py-1.5 text-zinc-300 transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Previous
              </button>
              <span className="px-2 py-1.5">
                {page} / {totalPages}
              </span>
              <button
                type="button"
                disabled={page >= totalPages}
                onClick={() => setPage((current) => current + 1)}
                className="rounded-lg bg-zinc-800 px-3 py-1.5 text-zinc-300 transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="flex flex-col gap-4">
        <h3 className="text-sm font-medium uppercase tracking-wide text-zinc-500">
          Storage
        </h3>

        {storageLoading ? (
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-4 py-8 text-center text-sm text-zinc-500">
            Loading disk stats...
          </div>
        ) : disks.length === 0 ? (
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-4 py-8 text-center text-sm text-zinc-500">
            No disks found
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {disks.map((disk) => (
              <StorageDiskCard key={disk.id} disk={disk} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
