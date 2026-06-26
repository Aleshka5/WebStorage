import { useCallback, useState } from "react";
import { getApiErrorDetail } from "../services/api";
import { uploadFile } from "../services/filesApi";

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
        id: crypto.randomUUID(),
        name: file.name,
        progress: 0,
        status: "uploading",
      }));

      setUploads((current) => [...current, ...pendingUploads]);

      await Promise.all(
        files.map(async (file, index) => {
          const uploadId = pendingUploads[index].id;

          try {
            await uploadFile(apiPrefix, path, file, (progress) => {
              setUploads((current) =>
                current.map((item) =>
                  item.id === uploadId ? { ...item, progress } : item,
                ),
              );
            });

            setUploads((current) =>
              current.map((item) =>
                item.id === uploadId
                  ? { ...item, progress: 100, status: "done" }
                  : item,
              ),
            );
          } catch (error) {
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
