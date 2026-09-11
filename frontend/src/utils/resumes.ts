export const RESERVED_RESUME_NAMES = ["meta.yaml", "statuses.yaml"];

export const HEX_COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;

export const DEFAULT_STATUS_COLOR = "#38BDF8";

export const INLINE_RESUME_ERROR_CODES = [
  "RESUME_NAME_INVALID",
  "RESUME_DEPTH_INVALID",
  "RESUME_STATUS_INVALID",
  "RESUME_FIELD_INVALID",
  "RESUME_NODE_EXISTS",
  "RESUME_META_INVALID",
];

export function joinResumePath(parentPath: string, name: string): string {
  return parentPath ? `${parentPath}/${name}` : name;
}

export function buildResumeRoute(segments: string[]): string {
  return ["/resumes", ...segments.map((segment) => encodeURIComponent(segment))].join("/");
}

export function validateResumeNodeName(name: string): string | undefined {
  const trimmed = name.trim();

  if (!trimmed) {
    return "Enter a name";
  }

  if (trimmed === "." || trimmed === "..") {
    return "Invalid name";
  }

  if (/[/\\]/.test(trimmed)) {
    return "Name cannot contain / or \\";
  }

  if (trimmed.length > 128) {
    return "Name cannot be longer than 128 characters";
  }

  if (RESERVED_RESUME_NAMES.includes(trimmed.toLowerCase())) {
    return "This name is reserved";
  }

  return undefined;
}

export function normalizeWebsiteUrl(value: string): string {
  const trimmed = value.trim();

  if (!trimmed) {
    return "";
  }

  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(trimmed)) {
    return trimmed;
  }

  return `https://${trimmed}`;
}

export function isInlineResumeErrorCode(errorCode?: string): boolean {
  return errorCode !== undefined && INLINE_RESUME_ERROR_CODES.includes(errorCode);
}
