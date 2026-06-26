import { useEffect } from "react";
import { useAuthStore } from "../../store/auth";
import { useQuotaStore } from "../../store/quota";

function formatUsedBytes(bytes: number): string {
  if (bytes === 0) {
    return "0 Б";
  }

  const megabytes = bytes / (1024 * 1024);
  return `${Math.round(megabytes)} МБ`;
}

function formatLimitBytes(bytes: number, role: string): string {
  if (role === "STRANGER") {
    return `${Math.round(bytes / (1024 * 1024))} МБ`;
  }

  const gigabytes = bytes / (1024 * 1024 * 1024);
  return `${Math.round(gigabytes * 10) / 10} ГБ`;
}

export function StorageUsageBar() {
  const user = useAuthStore((state) => state.user);
  const quota = useQuotaStore((state) => state.quota);
  const isLoading = useQuotaStore((state) => state.isLoading);
  const fetchQuota = useQuotaStore((state) => state.fetchQuota);

  useEffect(() => {
    void fetchQuota();
  }, [fetchQuota]);

  if (isLoading || !quota || !user) {
    return (
      <div className="border-t border-zinc-800 px-4 py-3">
        <div className="mb-1.5 h-2 w-full overflow-hidden rounded-full bg-zinc-800" />
        <span className="text-xs text-zinc-500">Загрузка...</span>
      </div>
    );
  }

  const usedPercent =
    quota.limit_bytes > 0
      ? Math.min(100, Math.round((quota.used_bytes / quota.limit_bytes) * 100))
      : 0;

  return (
    <div className="border-t border-zinc-800 px-4 py-3">
      <div className="mb-1.5 h-2 w-full overflow-hidden rounded-full bg-zinc-800">
        <div
          className="h-full rounded-full bg-sky-500 transition-all duration-300"
          style={{ width: `${usedPercent}%` }}
        />
      </div>
      <span className="text-xs text-zinc-400">
        {formatUsedBytes(quota.used_bytes)} из {formatLimitBytes(quota.limit_bytes, user.role)}
      </span>
    </div>
  );
}
