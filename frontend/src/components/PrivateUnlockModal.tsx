import { FormEvent, useEffect, useState } from "react";
import { isAxiosError } from "axios";
import { Lock } from "lucide-react";
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
      setError("Введите кодовое слово");
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

      setError("Неверное кодовое слово");
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
        setError(getErrorMessage(detail?.error_code, {
          available_bytes: detail?.available_bytes,
          retry_after: detail?.retry_after,
        }, detail?.message ?? "Не удалось разблокировать раздел"));
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
      setHint("Придумайте и введите новое кодовое слово");
    } catch (err) {
      const detail = getApiErrorDetail(err);
      setError(detail?.message ?? "Не удалось сбросить приватное хранилище");
    } finally {
      setIsResetting(false);
    }
  };

  const isBusy = isSubmitting || isResetting;

  if (!isOpen) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="private-unlock-title"
    >
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" aria-hidden="true" />
      <div className="relative z-10 w-full max-w-sm rounded-xl border border-zinc-700 bg-zinc-900 p-6 shadow-2xl">
        <div className="mb-4 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-sky-500/10 text-sky-400">
            <Lock className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h2 id="private-unlock-title" className="text-lg font-semibold text-zinc-100">
              Приватный раздел
            </h2>
            <p className="text-sm text-zinc-400">
              {hint ?? "Введите кодовое слово для доступа"}
            </p>
          </div>
        </div>

        <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
          <Input
            label="Кодовое слово"
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
          <div className="flex gap-2">
            <Button type="button" variant="secondary" onClick={onCancel} disabled={isBusy}>
              Отмена
            </Button>
            <Button type="submit" isLoading={isSubmitting} disabled={isResetting}>
              Войти в раздел
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
              Сбросить приватное хранилище вместе с кодом
            </Button>
          )}
        </form>
      </div>
    </div>
  );
}
