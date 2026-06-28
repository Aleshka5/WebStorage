import { formatBytes } from "../../utils/format";

export interface ErrorMessageOptions {
  available_bytes?: number;
  retry_after?: number;
}

const ERROR_MESSAGES: Record<string, string> = {
  QUOTA_EXCEEDED: "Недостаточно места. Освободите {available} для продолжения",
  PRIVATE_SESSION_EXPIRED: "Сессия истекла. Введите кодовое слово снова",
  DISK_UNAVAILABLE: "Хранилище временно недоступно. Попробуйте позже",
  TOO_MANY_ATTEMPTS: "Слишком много попыток. Подождите {retry_after} минут",
  ACCESS_DENIED: "Нет доступа к этому разделу",
  FILE_NOT_FOUND: "Файл не найден или был удалён",
  INTERNAL_ERROR: "Произошла ошибка. Попробуйте позже",
  UNSUPPORTED_FORMAT: "Неподдерживаемый формат файла",
  PATH_TRAVERSAL_DETECTED: "Недопустимый путь к файлу",
  UNAUTHORIZED: "Требуется авторизация",
  INVALID_CREDENTIALS: "Неверный email или пароль",
  EMAIL_ALREADY_EXISTS: "Пользователь с таким email уже существует",
  NOT_IMPLEMENTED: "Функция пока недоступна",
};

function applyPlaceholders(template: string, options?: ErrorMessageOptions): string {
  let text = template;

  if (text.includes("{available}")) {
    const available =
      options?.available_bytes !== undefined
        ? formatBytes(options.available_bytes, false)
        : "место";
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
    return `Ошибка: ${errorCode}`;
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
