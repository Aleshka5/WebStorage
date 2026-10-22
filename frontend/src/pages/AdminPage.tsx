import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { PageFrame } from "../components/Layout/PageFrame";
import { Button } from "../components/ui/Button";
import { LoadingBlock } from "../components/ui/LoadingBlock";
import {
  blockUser,
  deleteUser,
  getStorageStats,
  listUsers,
  updateUserQuota,
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

function bytesToMb(bytes: number): number {
  return Math.round((bytes / (1024 * 1024)) * 10) / 10;
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
    case "LOW_SPACE":
      return "tag";
    case "UNAVAILABLE":
      return "status-blocked";
    default:
      return "muted";
  }
}

function StorageDiskCard({ disk }: { disk: DiskStat }) {
  const usedPercent =
    disk.total_bytes > 0
      ? Math.min(100, Math.round((disk.used_bytes / disk.total_bytes) * 100))
      : 0;

  return (
    <article className="surface stack-tight">
      <div className="toolbar">
        <div>
          <h3>{disk.id}</h3>
          <p className="muted">Bucket: {disk.bucket}</p>
        </div>
        <span className={diskStatusClass(disk.status)}>{diskStatusLabel(disk.status)}</span>
      </div>
      <div className="meter" aria-hidden="true">
        <span style={{ width: `${usedPercent}%` }} />
      </div>
      <dl className="a-deflist">
        <div>
          <dt>Total</dt>
          <dd>{formatDiskBytes(disk.total_bytes)}</dd>
        </div>
        <div>
          <dt>Used</dt>
          <dd>{formatDiskBytes(disk.used_bytes)}</dd>
        </div>
        <div>
          <dt>Free</dt>
          <dd>{formatDiskBytes(disk.free_bytes)}</dd>
        </div>
      </dl>
    </article>
  );
}

interface QuotaInputProps {
  user: UserAdminView;
  onSaved: () => void;
}

interface NumericQuotaInputProps {
  displayValue: number;
  step: number;
  ariaLabel: string;
  onSave: (parsed: number) => Promise<void>;
}

function NumericQuotaInput({
  displayValue,
  step,
  ariaLabel,
  onSave,
}: NumericQuotaInputProps) {
  const [value, setValue] = useState(String(displayValue));
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    setValue(String(displayValue));
  }, [displayValue]);

  const handleBlur = async () => {
    const parsed = Number.parseFloat(value.replace(",", "."));
    if (Number.isNaN(parsed) || parsed < 0) {
      setValue(String(displayValue));
      return;
    }

    if (parsed === displayValue) {
      return;
    }

    setIsSaving(true);
    try {
      await onSave(parsed);
    } catch {
      setValue(String(displayValue));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <input
      type="number"
      min={0}
      step={step}
      value={value}
      disabled={isSaving}
      onChange={(event) => setValue(event.target.value)}
      onBlur={() => void handleBlur()}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.currentTarget.blur();
        }
      }}
      className="bare-input quota-input"
      aria-label={ariaLabel}
    />
  );
}

function TotalQuotaInput({ user, onSaved }: QuotaInputProps) {
  return (
    <NumericQuotaInput
      displayValue={bytesToMb(user.limit_bytes)}
      step={1}
      ariaLabel={`Total quota for ${user.email}`}
      onSave={async (limitMb) => {
        await updateUserQuota(user.id, { limit_mb: limitMb });
        onSaved();
      }}
    />
  );
}

function PrivateQuotaInput({ user, onSaved }: QuotaInputProps) {
  return (
    <NumericQuotaInput
      displayValue={bytesToGb(user.private_limit_bytes)}
      step={0.1}
      ariaLabel={`Private quota for ${user.email}`}
      onSave={async (privateLimitGb) => {
        await updateUserQuota(user.id, { private_limit_gb: privateLimitGb });
        onSaved();
      }}
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
    <PageFrame title="Admin" lead="Quotas, accounts, and disks for this storage service.">
      <section className="a-section">
        <h2>Users</h2>
        <p className="role-note">Role is changed in Auth-Service /admin.</p>

        <div className="filters">
          <div className="segment" role="group" aria-label="Role">
            {(["ALL", ...ROLES] as const).map((role) => (
              <button
                key={role}
                type="button"
                aria-pressed={roleFilter === role}
                onClick={() => {
                  setRoleFilter(role);
                  setPage(1);
                }}
              >
                {role === "ALL" ? "All" : role}
              </button>
            ))}
          </div>
          <label htmlFor="user-email">Email</label>
          <input
            id="user-email"
            name="user-email"
            type="search"
            placeholder="Filter by email"
            autoComplete="off"
            spellCheck={false}
            value={emailSearch}
            onChange={(event) => setEmailSearch(event.target.value)}
          />
        </div>

        {error && <div className="banner error">{error}</div>}

        <div className="table-wrap table-dense">
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Used</th>
                <th>Quota, MB</th>
                <th>Private, GB</th>
                <th>Registered</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td colSpan={8}>
                    <LoadingBlock active label="Loading users…" />
                  </td>
                </tr>
              ) : users.length === 0 ? (
                <tr>
                  <td colSpan={8}>
                    <p className="empty">No one matches that email.</p>
                  </td>
                </tr>
              ) : (
                users.map((user) => (
                  <tr key={user.id}>
                    <td className="cell-email" data-label="Email">
                      {user.email}
                    </td>
                    <td
                      data-label="Role"
                      className={user.role === "BLOCKED" ? "status-blocked" : undefined}
                    >
                      {user.role}
                    </td>
                    <td
                      data-label="Status"
                      className={user.is_active ? undefined : "status-blocked"}
                    >
                      {user.is_active ? "Active" : "Blocked"}
                    </td>
                    <td data-label="Used">{formatBytes(user.quota_used_bytes, false)}</td>
                    <td data-label="Quota, MB">
                      <TotalQuotaInput user={user} onSaved={() => void fetchUsers()} />
                    </td>
                    <td data-label="Private, GB">
                      <PrivateQuotaInput user={user} onSaved={() => void fetchUsers()} />
                    </td>
                    <td data-label="Registered">{formatDateTime(user.created_at)}</td>
                    <td data-label="Actions">
                      <div className="actions">
                        <Button
                          variant="danger"
                          className="btn-small"
                          disabled={!user.is_active || user.id === currentUser.user_id}
                          onClick={() => void handleBlock(user)}
                        >
                          Block
                        </Button>
                        <Button
                          variant="secondary"
                          className="btn-small"
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
          <div className="pager">
            <span>
              Showing {users.length} of {total}
            </span>
            <div className="actions">
              <button
                type="button"
                className="btn btn-ghost"
                disabled={page <= 1}
                onClick={() => setPage((current) => current - 1)}
              >
                Previous
              </button>
              <span>
                {page} / {totalPages}
              </span>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={page >= totalPages}
                onClick={() => setPage((current) => current + 1)}
              >
                Next
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="a-section">
        <h2>Storage</h2>
        {storageLoading ? (
          <LoadingBlock active label="Loading disks…" />
        ) : disks.length === 0 ? (
          <p className="empty">No disks found.</p>
        ) : (
          <div className="card-grid">
            {disks.map((disk) => (
              <StorageDiskCard key={disk.id} disk={disk} />
            ))}
          </div>
        )}
      </section>
    </PageFrame>
  );
}
