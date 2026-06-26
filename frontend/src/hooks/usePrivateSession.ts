import { useCallback, useEffect, useState } from "react";
import { EVENT_PRIVATE_EXPIRED } from "../services/api";
import { getPrivateSession } from "../services/privateApi";

interface UsePrivateSessionResult {
  isActive: boolean;
  isLoading: boolean;
  showUnlockModal: boolean;
  refreshSession: () => Promise<void>;
  onUnlockSuccess: () => void;
}

export function usePrivateSession(): UsePrivateSessionResult {
  const [isActive, setIsActive] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [showUnlockModal, setShowUnlockModal] = useState(false);

  const refreshSession = useCallback(async () => {
    setIsLoading(true);

    try {
      const session = await getPrivateSession();
      setIsActive(session.active);
      setShowUnlockModal(!session.active);
    } catch {
      setIsActive(false);
      setShowUnlockModal(true);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  useEffect(() => {
    const handleSessionExpired = () => {
      setIsActive(false);
      setShowUnlockModal(true);
    };

    window.addEventListener(EVENT_PRIVATE_EXPIRED, handleSessionExpired);
    return () => window.removeEventListener(EVENT_PRIVATE_EXPIRED, handleSessionExpired);
  }, []);

  const onUnlockSuccess = useCallback(() => {
    setIsActive(true);
    setShowUnlockModal(false);
  }, []);

  return {
    isActive,
    isLoading,
    showUnlockModal,
    refreshSession,
    onUnlockSuccess,
  };
}
