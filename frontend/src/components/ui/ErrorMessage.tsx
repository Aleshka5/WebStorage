const ERROR_MESSAGES: Record<string, string> = {
  QUOTA_EXCEEDED: "Недостаточно места. Освободите место для продолжения",
  UNSUPPORTED_FORMAT: "Неподдерживаемый формат файла",
  PRIVATE_SESSION_EXPIRED: "Сессия истекла. Введите кодовое слово снова",
  DISK_UNAVAILABLE: "Хранилище временно недоступно. Попробуйте позже",
  PATH_TRAVERSAL_DETECTED: "Недопустимый путь к файлу",
  FILE_NOT_FOUND: "Файл не найден или был удалён",
  ACCESS_DENIED: "Нет доступа к этому разделу",
  UNAUTHORIZED: "Требуется авторизация",
  INVALID_CREDENTIALS: "Неверный email или пароль",
  EMAIL_ALREADY_EXISTS: "Пользователь с таким email уже существует",
  TOO_MANY_ATTEMPTS: "Слишком много попыток. Попробуйте позже",
  NOT_IMPLEMENTED: "Функция пока недоступна",
  INTERNAL_ERROR: "Произошла ошибка. Попробуйте позже",
};

interface ErrorMessageProps {
  errorCode?: string | null;
  message?: string;
  className?: string;
}

export function ErrorMessage({ errorCode, message, className = "" }: ErrorMessageProps) {
  const text =
    (errorCode && ERROR_MESSAGES[errorCode]) ||
    message ||
    (errorCode ? `Ошибка: ${errorCode}` : "Произошла ошибка");

  return <p className={`text-sm text-red-400 ${className}`.trim()}>{text}</p>;
}
