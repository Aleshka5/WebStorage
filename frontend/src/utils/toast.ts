import toast from "react-hot-toast";
import { getApiErrorDetail, type ApiErrorDetail } from "../services/api";
import { getErrorMessage } from "../components/ui/ErrorMessage";

export function showErrorToast(error: unknown): void {
  const detail = getApiErrorDetail(error);
  toast.error(formatApiError(detail));
}

export function formatApiError(detail: ApiErrorDetail | null): string {
  return getErrorMessage(
    detail?.error_code ?? "INTERNAL_ERROR",
    {
      available_bytes: detail?.available_bytes,
      retry_after: detail?.retry_after,
    },
    detail?.message,
  );
}

export function showSuccessToast(message: string): void {
  toast.success(message);
}

export const LARGE_UPLOAD_THRESHOLD_BYTES = 5 * 1024 * 1024;

export function showUploadProgressToast(toastId: string, fileName: string, progress: number): void {
  toast.loading(`Загрузка ${fileName}... ${progress}%`, { id: toastId });
}

export function dismissToast(toastId: string): void {
  toast.dismiss(toastId);
}
