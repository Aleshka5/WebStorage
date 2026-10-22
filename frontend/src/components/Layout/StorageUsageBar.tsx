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
      <div>
        <div className="meter" aria-hidden="true" />
        <p className="meter-label">Loading storage…</p>
      </div>
    );
  }

  const usedPercent =
    quota.limit_bytes > 0
      ? Math.min(100, Math.round((quota.used_bytes / quota.limit_bytes) * 100))
      : 0;
  const limitLabel = quota.limit_bytes > 0 ? formatBytes(quota.limit_bytes, false) : "Unlimited";

  return (
    <div>
      <div className="meter" aria-hidden="true">
        <span style={{ width: `${usedPercent}%` }} />
      </div>
      <p className="meter-label">
        {formatBytes(quota.used_bytes, false)} of {limitLabel}
      </p>
    </div>
  );
}
