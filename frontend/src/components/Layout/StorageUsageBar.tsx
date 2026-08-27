import { useEffect } from "react";
import { useAuthStore } from "../../store/auth";
import { useQuotaStore } from "../../store/quota";
import { formatBytes } from "../../utils/format";

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
        <span className="text-xs text-zinc-500">Loading...</span>
      </div>
    );
  }

  const usedPercent =
    quota.limit_bytes > 0
      ? Math.min(100, Math.round((quota.used_bytes / quota.limit_bytes) * 100))
      : 0;
  const limitLabel =
    quota.limit_bytes > 0 ? formatBytes(quota.limit_bytes, false) : "Unlimited";

  return (
    <div className="border-t border-zinc-800 px-4 py-3">
      <div className="mb-1.5 h-2 w-full overflow-hidden rounded-full bg-zinc-800">
        <div
          className="h-full rounded-full bg-sky-500 transition-all duration-300"
          style={{ width: `${usedPercent}%` }}
        />
      </div>
      <span className="text-xs text-zinc-400">
        {formatBytes(quota.used_bytes, false)} of {limitLabel}
      </span>
    </div>
  );
}
