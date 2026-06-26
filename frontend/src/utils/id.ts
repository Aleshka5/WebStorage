/**
 * crypto.randomUUID() доступен только в secure context (HTTPS / localhost).
 * При доступе с iPhone по http://192.168.x.x вызов бросает исключение.
 */
export function generateId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    try {
      return crypto.randomUUID();
    } catch {
      // Non-secure context (e.g. LAN HTTP on mobile).
    }
  }

  return `${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}
