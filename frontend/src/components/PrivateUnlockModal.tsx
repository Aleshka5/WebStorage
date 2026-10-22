import { FormEvent, useEffect, useState } from "react";
import { isAxiosError } from "axios";
import { resetPrivateStorage, unlockPrivate } from "../services/privateApi";
import { getApiErrorDetail } from "../services/api";
import { getErrorMessage } from "./ui/ErrorMessage";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";

interface PrivateUnlockModalProps {
  isOpen: boolean;
  onSuccess: () => void;
  onCancel: () => void;
}

function formatRateLimitMessage(retryAfterSeconds: number): string {
  return getErrorMessage("TOO_MANY_ATTEMPTS", { retry_after: retryAfterSeconds });
}

export function PrivateUnlockModal({ isOpen, onSuccess, onCancel }: PrivateUnlockModalProps) {
  const [passphrase, setPassphrase] = useState("");
  const [error, setError] = useState<string | undefined>();
  const [hint, setHint] = useState<string | undefined>();
  const [isRateLimited, setIsRateLimited] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isResetting, setIsResetting] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      setPassphrase("");
      setError(undefined);
      setHint(undefined);
      setIsRateLimited(false);
      setIsSubmitting(false);
      setIsResetting(false);
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const trimmed = passphrase.trim();
    if (!trimmed) {
      setError("Enter a passphrase");
      return;
    }

    setIsSubmitting(true);
    setError(undefined);

    try {
      const result = await unlockPrivate(trimmed);

      if (result.success) {
        onSuccess();
        return;
      }

      setError("Incorrect passphrase");
    } catch (err) {
      if (isAxiosError(err) && err.response?.status === 429) {
        const detail = err.response.data?.detail;
        const retryAfter =
          detail && typeof detail === "object" && "retry_after" in detail
            ? Number(detail.retry_after)
            : 900;
        setError(formatRateLimitMessage(retryAfter));
        setIsRateLimited(true);
      } else {
        const detail = getApiErrorDetail(err);
        setError(
          getErrorMessage(
            detail?.error_code,
            {
              available_bytes: detail?.available_bytes,
              retry_after: detail?.retry_after,
            },
            detail?.message ?? "Failed to unlock this section",
          ),
        );
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleReset = async () => {
    setIsResetting(true);
    setError(undefined);

    try {
      await resetPrivateStorage();
      setIsRateLimited(false);
      setPassphrase("");
      setHint("Create and enter a new passphrase");
    } catch (err) {
      const detail = getApiErrorDetail(err);
      setError(detail?.message ?? "Failed to reset private storage");
    } finally {
      setIsResetting(false);
    }
  };

  const isBusy = isSubmitting || isResetting;

  if (!isOpen) {
    return null;
  }

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="private-unlock-title">
      <div className="overlay-dismiss" aria-hidden="true" />
      <div className="panel stack">
        <p className="kicker">Storage</p>
        <h2 id="private-unlock-title">Private section</h2>
        <p className="muted">{hint ?? "Enter your passphrase to unlock"}</p>
        <form className="stack" onSubmit={handleSubmit}>
          <Input
            label="Passphrase"
            type="password"
            value={passphrase}
            onChange={(event) => {
              setPassphrase(event.target.value);
              if (error && !isRateLimited) {
                setError(undefined);
              }
            }}
            error={error}
            autoFocus
            disabled={isBusy}
            autoComplete="off"
          />
          <div className="actions">
            <Button type="button" variant="secondary" onClick={onCancel} disabled={isBusy}>
              Cancel
            </Button>
            <Button type="submit" isLoading={isSubmitting} disabled={isResetting}>
              Unlock
            </Button>
          </div>
          {isRateLimited && (
            <Button
              type="button"
              variant="danger"
              onClick={handleReset}
              isLoading={isResetting}
              disabled={isSubmitting}
            >
              Reset private storage and passphrase
            </Button>
          )}
        </form>
      </div>
    </div>
  );
}
