import api from "./api";
import type { FileNode } from "../types/files";

export async function listFiles(apiPrefix: string, path: string): Promise<FileNode[]> {
  const { data } = await api.get<FileNode[]>(apiPrefix, { params: { path } });
  return data;
}

export async function uploadFile(
  apiPrefix: string,
  path: string,
  file: File,
  onProgress?: (progress: number) => void,
): Promise<void> {
  const formData = new FormData();
  formData.append("file", file);

  await api.post(`${apiPrefix}/upload`, formData, {
    params: { path },
    headers: { "Content-Type": "multipart/form-data" },
    onUploadProgress: (event) => {
      if (!onProgress || !event.total) {
        return;
      }
      onProgress(Math.round((event.loaded * 100) / event.total));
    },
  });
}

export async function downloadFile(
  apiPrefix: string,
  path: string,
  filename: string,
): Promise<void> {
  const response = await api.get(`${apiPrefix}/download`, {
    params: { path },
    responseType: "blob",
  });

  const url = window.URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  window.URL.revokeObjectURL(url);
}

export async function deleteFile(apiPrefix: string, path: string): Promise<void> {
  await api.delete(apiPrefix, { params: { path } });
}

export async function createDirectory(
  apiPrefix: string,
  path: string,
  name: string,
): Promise<void> {
  await api.post(`${apiPrefix}/mkdir`, { path, name });
}

export async function renameEntry(
  apiPrefix: string,
  path: string,
  newName: string,
): Promise<void> {
  await api.patch(`${apiPrefix}/rename`, { path, new_name: newName });
}
