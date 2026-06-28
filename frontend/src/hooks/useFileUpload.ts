import { useCallback, useState } from "react";
import { getApiErrorDetail } from "../services/api";
import { uploadFile } from "../services/filesApi";
import {
  dismissToast,
  LARGE_UPLOAD_THRESHOLD_BYTES,
  showErrorToast,
  showSuccessToast,
  showUploadProgressToast,
} from "../utils/toast";
import { generateId } from "../utils/id";

export type UploadStatus = "uploading" | "done" | "error";

export interface UploadFileState {
  id: string;
  name: string;
  progress: number;
  status: UploadStatus;
  error_code?: string;
}

export function useFileUpload(apiPrefix: string) {
  const [uploads, setUploads] = useState<UploadFileState[]>([]);

  const uploadFiles = useCallback(
    async (files: File[], path: string) => {
      if (files.length === 0) {
        return;
      }

      const pendingUploads: UploadFileState[] = files.map((file) => ({
        id: generateId(),
        name: file.name,
        progress: 0,
        status: "uploading",
      }));

      setUploads((current) => [...current, ...pendingUploads]);

      await Promise.all(
        files.map(async (file, index) => {
          const uploadId = pendingUploads[index].id;
          const progressToastId = `upload-${uploadId}`;
          const showProgressToast = file.size > LARGE_UPLOAD_THRESHOLD_BYTES;

          if (showProgressToast) {
            showUploadProgressToast(progressToastId, file.name, 0);
          }

          try {
            await uploadFile(apiPrefix, path, file, (progress) => {
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
                  ? { ...item, progress: 100, status: "done" }
                  : item,
              ),
            );
            showSuccessToast("Файл загружен");
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
    [apiPrefix],
  );

  const clearFinished = useCallback(() => {
    setUploads((current) => current.filter((item) => item.status === "uploading"));
  }, []);

  return { uploads, uploadFiles, clearFinished };
}
