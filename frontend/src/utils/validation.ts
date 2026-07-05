const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateEmail(email: string): string | undefined {
  if (!email.trim()) {
    return "Enter email";
  }
  if (!EMAIL_PATTERN.test(email.trim())) {
    return "Invalid email format";
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
    return "Passwords do not match";
  }
  return undefined;
}

const INVALID_FILE_NAME_CHARS = /[/\\:*?"<>|]/;

export function validateFileName(name: string): string | undefined {
  const trimmed = name.trim();

  if (!trimmed) {
    return "Enter name";
  }

  if (trimmed === "." || trimmed === "..") {
    return "Invalid name";
  }

  if (INVALID_FILE_NAME_CHARS.test(trimmed)) {
    return 'Name contains invalid characters: / \ : * ? " < > |';
  }

  return undefined;
}
