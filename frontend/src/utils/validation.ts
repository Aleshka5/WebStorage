const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateEmail(email: string): string | undefined {
  if (!email.trim()) {
    return "Введите email";
  }
  if (!EMAIL_PATTERN.test(email.trim())) {
    return "Некорректный формат email";
  }
  return undefined;
}

export function validateRequired(value: string, message: string): string | undefined {
  if (!value.trim()) {
    return message;
  }
  return undefined;
}

export function validatePasswordMatch(
  password: string,
  confirmPassword: string,
): string | undefined {
  if (password !== confirmPassword) {
    return "Пароли не совпадают";
  }
  return undefined;
}

const INVALID_FILE_NAME_CHARS = /[/\\:*?"<>|]/;

export function validateFileName(name: string): string | undefined {
  const trimmed = name.trim();

  if (!trimmed) {
    return "Введите имя";
  }

  if (trimmed === "." || trimmed === "..") {
    return "Недопустимое имя";
  }

  if (INVALID_FILE_NAME_CHARS.test(trimmed)) {
    return 'Имя содержит недопустимые символы: / \\ : * ? " < > |';
  }

  return undefined;
}
