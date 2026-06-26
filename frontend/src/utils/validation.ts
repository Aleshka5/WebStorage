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
