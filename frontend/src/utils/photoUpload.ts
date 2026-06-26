const MIME_TO_EXTENSION: Record<string, string> = {
  "image/jpeg": ".jpg",
  "image/png": ".png",
  "image/webp": ".webp",
  "image/gif": ".gif",
  "image/heic": ".heic",
  "image/heif": ".heic",
};

function extensionFromMime(mimeType: string): string {
  return MIME_TO_EXTENSION[mimeType.toLowerCase()] ?? ".jpg";
}

function hasValidFilename(name: string): boolean {
  const trimmed = name.trim();

  if (!trimmed || trimmed === "." || trimmed === "..") {
    return false;
  }

  return trimmed.includes(".") && trimmed.length > 1;
}

/**
 * iOS camera / picker often returns files without a usable filename.
 * FastAPI rejects uploads when the multipart part has no filename.
 */
export function normalizePhotoFile(file: File): File {
  const name = file.name?.trim() ?? "";

  if (hasValidFilename(name)) {
    return file;
  }

  const extension = extensionFromMime(file.type);
  const normalizedName = `photo-${Date.now()}${extension}`;

  return new File([file], normalizedName, {
    type: file.type || "image/jpeg",
    lastModified: file.lastModified,
  });
}

export function normalizePhotoFiles(files: File[]): File[] {
  return files.map(normalizePhotoFile);
}
