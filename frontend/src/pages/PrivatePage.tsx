import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileManager } from "../components/FileManager/FileManager";
import { PageFrame } from "../components/Layout/PageFrame";
import { PrivateUnlockModal } from "../components/PrivateUnlockModal";
import { LoadingBlock } from "../components/ui/LoadingBlock";
import { usePrivateSession } from "../hooks/usePrivateSession";
import { getPrivateQuota, type PrivateQuota } from "../services/privateApi";

function formatPrivateUsedBytes(bytes: number): string {
  if (bytes === 0) {
    return "0 MB";
  }

  const megabytes = bytes / (1024 * 1024);
  return `${Math.round(megabytes)} MB`;
}

function formatPrivateLimitBytes(bytes: number): string {
  const gigabytes = bytes / (1024 * 1024 * 1024);
  return `${Math.round(gigabytes * 10) / 10} GB`;
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
      <div className="surface-tight">
        <div className="meter" aria-hidden="true" />
        <p className="meter-label">Loading private storage…</p>
      </div>
    );
  }

  const usedPercent =
    quota.private_limit_bytes > 0
      ? Math.min(100, Math.round((quota.private_bytes / quota.private_limit_bytes) * 100))
      : 0;

  return (
    <div className="surface-tight">
      <div className="meter" aria-hidden="true">
        <span style={{ width: `${usedPercent}%` }} />
      </div>
      <p className="meter-label">
        Private: {formatPrivateUsedBytes(quota.private_bytes)} of{" "}
        {formatPrivateLimitBytes(quota.private_limit_bytes)}
      </p>
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

  return (
    <PageFrame title="Private" lead="Encrypted files. A passphrase unlocks them on this device.">
      {isLoading ? <LoadingBlock active label="Loading private files…" /> : null}
      {isActive && (
        <>
          <PrivateQuotaBar refreshKey={quotaRefreshKey} />
          <FileManager apiPrefix="/api/private" mode="encrypted" />
        </>
      )}
      <PrivateUnlockModal
        isOpen={showUnlockModal}
        onSuccess={handleUnlockSuccess}
        onCancel={handleCancel}
      />
    </PageFrame>
  );
}
