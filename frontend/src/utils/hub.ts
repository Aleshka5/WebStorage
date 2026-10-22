const HUB_ORIGIN = "https://filenkov.store";

export function signInHref(): string {
  return `${HUB_ORIGIN}/auth?return_to=${encodeURIComponent(window.location.href)}`;
}

export function hubHref(): string {
  return HUB_ORIGIN;
}

export function displayName(email: string | undefined): string {
  if (!email) {
    return "Signed in";
  }

  const separator = email.indexOf("@");
  const local = separator > 0 ? email.slice(0, separator) : email;
  return local || "Signed in";
}
