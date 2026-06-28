import { useCallback, useState } from "react";
import { getApiErrorDetail } from "../services/api";
import { uploadPhoto } from "../services/photosApi";
import type { PhotoItem } from "../types/photos";
import {
  dismissToast,
  LARGE_UPLOAD_THRESHOLD_BYTES,
  showErrorToast,
  showSuccessToast,
  showUploadProgressToast,
} from "../utils/toast";
import { normalizePhotoFiles } from "../utils/photoUpload";
import { generateId } from "../utils/id";

export type PhotoUploadStatus = "uploading" | "done" | "error";

export interface PhotoUploadState {
  id: string;
  name: string;
  progress: number;
  status: PhotoUploadStatus;
  error_code?: string;
  photo?: PhotoItem;
}

export function usePhotoUpload() {
  const [uploads, setUploads] = useState<PhotoUploadState[]>([]);

  const uploadPhotos = useCallback(
    async (files: File[], onUploaded?: (photo: PhotoItem) => void) => {
      if (files.length === 0) {
        return;
      }

      let normalizedFiles: File[];

      try {
        normalizedFiles = normalizePhotoFiles(files);
      } catch (error) {
        console.error("Failed to normalize photo files", error);
        return;
      }

      const pendingUploads: PhotoUploadState[] = normalizedFiles.map((file) => ({
        id: generateId(),
        name: file.name,
        progress: 0,
        status: "uploading",
      }));

      setUploads((current) => [...current, ...pendingUploads]);

      await Promise.all(
        normalizedFiles.map(async (file, index) => {
          const uploadId = pendingUploads[index].id;
          const progressToastId = `photo-upload-${uploadId}`;
          const showProgressToast = file.size > LARGE_UPLOAD_THRESHOLD_BYTES;

          if (showProgressToast) {
            showUploadProgressToast(progressToastId, file.name, 0);
          }

          try {
            const photo = await uploadPhoto(file, (progress) => {
              setUploads((current) =>
                current.map((item) =>
                  item.id === uploadId ? { ...item, progress } : item,
                ),
              );

              if (showProgressToast) {
                showUploadProgressToast(progressToastId, file.name, progress);
              }
            });

            if (showProgressToast) {
              dismissToast(progressToastId);
            }

            setUploads((current) =>
              current.map((item) =>
                item.id === uploadId
                  ? { ...item, progress: 100, status: "done", photo }
                  : item,
              ),
            );

            showSuccessToast("Файл загружен");
            onUploaded?.(photo);
          } catch (error) {
            if (showProgressToast) {
              dismissToast(progressToastId);
            }

            const detail = getApiErrorDetail(error);

            setUploads((current) =>
              current.map((item) =>
                item.id === uploadId
                  ? {
                      ...item,
                      status: "error",
                      error_code: detail?.error_code,
                    }
                  : item,
              ),
            );
            showErrorToast(error);
          }
        }),
      );
    },
    [],
  );

  const clearFinished = useCallback(() => {
    setUploads((current) => current.filter((item) => item.status === "uploading"));
  }, []);

  return { uploads, uploadPhotos, clearFinished };
}
