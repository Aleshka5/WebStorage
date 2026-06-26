import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileManager } from "../components/FileManager/FileManager";
import { PrivateUnlockModal } from "../components/PrivateUnlockModal";
import { usePrivateSession } from "../hooks/usePrivateSession";
import { getPrivateQuota, type PrivateQuota } from "../services/privateApi";

function formatPrivateUsedBytes(bytes: number): string {
  if (bytes === 0) {
    return "0 МБ";
  }

  const megabytes = bytes / (1024 * 1024);
  return `${Math.round(megabytes)} МБ`;
}

function formatPrivateLimitBytes(bytes: number): string {
  const gigabytes = bytes / (1024 * 1024 * 1024);
  return `${Math.round(gigabytes * 10) / 10} ГБ`;
}

function PrivateQuotaBar({ refreshKey }: { refreshKey: number }) {
  const [quota, setQuota] = useState<PrivateQuota | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchQuota = useCallback(async () => {
    setIsLoading(true);

    try {
      const data = await getPrivateQuota();
      setQuota(data);
    } catch {
      setQuota(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchQuota();
  }, [fetchQuota, refreshKey]);

  if (isLoading || !quota) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-4 py-3">
        <div className="mb-1.5 h-2 w-full overflow-hidden rounded-full bg-zinc-800" />
        <span className="text-xs text-zinc-500">Загрузка квоты...</span>
      </div>
    );
  }

  const usedPercent =
    quota.private_limit_bytes > 0
      ? Math.min(
          100,
          Math.round((quota.private_bytes / quota.private_limit_bytes) * 100),
        )
      : 0;

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-4 py-3">
      <div className="mb-1.5 h-2 w-full overflow-hidden rounded-full bg-zinc-800">
        <div
          className="h-full rounded-full bg-violet-500 transition-all duration-300"
          style={{ width: `${usedPercent}%` }}
        />
      </div>
      <span className="text-xs text-zinc-400">
        Приватное: {formatPrivateUsedBytes(quota.private_bytes)} из{" "}
        {formatPrivateLimitBytes(quota.private_limit_bytes)}
      </span>
    </div>
  );
}

export default function PrivatePage() {
  const navigate = useNavigate();
  const { isActive, isLoading, showUnlockModal, onUnlockSuccess } = usePrivateSession();
  const [quotaRefreshKey, setQuotaRefreshKey] = useState(0);

  const handleUnlockSuccess = useCallback(() => {
    onUnlockSuccess();
    setQuotaRefreshKey((key) => key + 1);
  }, [onUnlockSuccess]);

  const handleCancel = useCallback(() => {
    navigate("/files");
  }, [navigate]);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="h-8 w-8 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <h2 className="text-xl font-semibold text-zinc-100">Приватное</h2>

      {isActive && (
        <>
          <PrivateQuotaBar refreshKey={quotaRefreshKey} />
          <div className="min-h-0 flex-1">
            <FileManager apiPrefix="/api/private" mode="encrypted" />
          </div>
        </>
      )}

      <PrivateUnlockModal
        isOpen={showUnlockModal}
        onSuccess={handleUnlockSuccess}
        onCancel={handleCancel}
      />
    </div>
  );
}
