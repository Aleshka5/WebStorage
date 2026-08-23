import { formatBytes } from "../../utils/format";

export interface ErrorMessageOptions {
  available_bytes?: number;
  retry_after?: number;
}

const ERROR_MESSAGES: Record<string, string> = {
  QUOTA_EXCEEDED: "Not enough space. Free up {available} to continue",
  PRIVATE_SESSION_EXPIRED: "Session expired. Enter your passphrase again",
  DISK_UNAVAILABLE: "Storage is temporarily unavailable. Try again later",
  TOO_MANY_ATTEMPTS: "Too many attempts. Wait {retry_after} minutes",
  ACCESS_DENIED: "You do not have access to this section",
  FILE_NOT_FOUND: "File not found or has been deleted",
  INTERNAL_ERROR: "Something went wrong. Try again later",
  UNSUPPORTED_FORMAT: "Unsupported file format",
  PATH_TRAVERSAL_DETECTED: "Invalid file path",
  UNAUTHORIZED: "Authorization required",
  AUTH_UNAVAILABLE: "Authorization service is temporarily unavailable. Try again later",
  INVALID_CREDENTIALS: "Invalid email or password",
  EMAIL_ALREADY_EXISTS: "A user with this email already exists",
  NOT_IMPLEMENTED: "This feature is not available yet",
};

function applyPlaceholders(template: string, options?: ErrorMessageOptions): string {
  let text = template;

  if (text.includes("{available}")) {
    const available =
      options?.available_bytes !== undefined
        ? formatBytes(options.available_bytes, false)
        : "space";
    text = text.replace("{available}", available);
  }

  if (text.includes("{retry_after}")) {
    const retryAfterSeconds = options?.retry_after ?? 900;
    const minutes = Math.max(1, Math.ceil(retryAfterSeconds / 60));
    text = text.replace("{retry_after}", String(minutes));
  }

  return text;
}

export function getErrorMessage(
  errorCode?: string | null,
  options?: ErrorMessageOptions,
  fallbackMessage?: string,
): string {
  if (errorCode && ERROR_MESSAGES[errorCode]) {
    return applyPlaceholders(ERROR_MESSAGES[errorCode], options);
  }

  if (fallbackMessage) {
    return fallbackMessage;
  }

  if (errorCode) {
    return `Error: ${errorCode}`;
  }

  return ERROR_MESSAGES.INTERNAL_ERROR;
}

interface ErrorMessageProps {
  errorCode?: string | null;
  message?: string;
  available_bytes?: number;
  retry_after?: number;
  className?: string;
}

export function ErrorMessage({
  errorCode,
  message,
  available_bytes,
  retry_after,
  className = "",
}: ErrorMessageProps) {
  const text = getErrorMessage(
    errorCode,
    { available_bytes, retry_after },
    message,
  );

  return <p className={`text-sm text-red-400 ${className}`.trim()}>{text}</p>;
}
