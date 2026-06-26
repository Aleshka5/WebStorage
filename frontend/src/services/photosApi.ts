import api from "./api";
import type { PhotoItem, PhotoListResponse } from "../types/photos";
import { PHOTO_BATCH_SIZE } from "../types/photos";

export async function listPhotos(
  page: number,
  limit = PHOTO_BATCH_SIZE,
): Promise<PhotoListResponse> {
  const { data } = await api.get<PhotoListResponse>("/api/photos", {
    params: { page, limit },
  });
  return data;
}

export async function uploadPhoto(
  file: File,
  onProgress?: (progress: number) => void,
): Promise<PhotoItem> {
  const formData = new FormData();
  formData.append("file", file);

  const { data } = await api.post<PhotoItem>("/api/photos/upload", formData, {
    onUploadProgress: (event) => {
      if (!onProgress || !event.total) {
        return;
      }
      onProgress(Math.round((event.loaded * 100) / event.total));
    },
  });

  return data;
}

export async function deletePhoto(id: string): Promise<void> {
  await api.delete(`/api/photos/${id}`);
}

export async function downloadPhotoOriginal(
  originalUrl: string,
  filename: string,
): Promise<void> {
  const response = await api.get(originalUrl, { responseType: "blob" });

  const url = window.URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  window.URL.revokeObjectURL(url);
}
