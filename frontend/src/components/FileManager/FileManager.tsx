import {
  ChevronRight,
  FolderPlus,
  Lock,
  Package,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFileUpload } from "../../hooks/useFileUpload";
import { getApiErrorDetail } from "../../services/api";
import {
  createDirectory,
  deleteFile,
  downloadFile,
  downloadFolder,
  listFiles,
  renameEntry,
  uploadZipFolder,
} from "../../services/filesApi";
import { useQuotaStore } from "../../store/quota";
import type { FileManagerMode, FileNode, SortDirection, SortField } from "../../types/files";
import { ErrorMessage } from "../ui/ErrorMessage";
import { showErrorToast, showSuccessToast } from "../../utils/toast";
import { Button } from "../ui/Button";
import { Modal } from "../ui/Modal";
import { CreateFolderDialog } from "./CreateFolderDialog";
import { DropZone } from "./DropZone";
import { FileList } from "./FileList";

interface FileManagerProps {
  apiPrefix: string;
  mode: FileManagerMode;
  basePath?: string;
  hiddenNames?: string[];
}

function normalizeBasePath(basePath?: string): string {
  return (basePath ?? "").replace(/\\/g, "/").replace(/^\/+/, "").replace(/\/+$/, "");
}

function toApiPath(basePath: string, path: string): string {
  if (!basePath) {
    return path;
  }

  const suffix = path.replace(/^\/+/, "");

  return suffix ? `${basePath}/${suffix}` : basePath;
}

function toLocalPath(basePath: string, path: string): string {
  if (!basePath) {
    return path;
  }

  const normalized = path.replace(/\\/g, "/").replace(/^\/+/, "");

  if (normalized === basePath) {
    return "/";
  }

  if (normalized.startsWith(`${basePath}/`)) {
    return `/${normalized.slice(basePath.length + 1)}`;
  }

  return path;
}

function sortItems(
  items: FileNode[],
  sortField: SortField,
  sortDirection: SortDirection,
): FileNode[] {
  const directionMultiplier = sortDirection === "asc" ? 1 : -1;

  return [...items].sort((left, right) => {
    if (left.is_dir !== right.is_dir) {
      return left.is_dir ? -1 : 1;
    }

    let comparison = 0;

    if (sortField === "name") {
      const leftName = left.name ?? "";
      const rightName = right.name ?? "";
      comparison = leftName.localeCompare(rightName, "en");
    } else if (sortField === "size") {
      comparison = (left.size ?? 0) - (right.size ?? 0);
    } else {
      comparison =
        (new Date(left.modified_at).getTime() ?? 0) -
        (new Date(right.modified_at).getTime() ?? 0);
    }

    return comparison * directionMultiplier;
  });
}

function buildBreadcrumbs(
  currentPath: string,
  rootLabel: string,
): Array<{ label: string; path: string }> {
  const normalized = currentPath.replace(/\\/g, "/").replace(/\/+$/, "") || "/";

  if (normalized === "/") {
    return [{ label: rootLabel, path: "/" }];
  }

  const segments = normalized.split("/").filter(Boolean);
  const crumbs: Array<{ label: string; path: string }> = [{ label: rootLabel, path: "/" }];

  segments.forEach((segment, index) => {
    crumbs.push({
      label: segment,
      path: `/${segments.slice(0, index + 1).join("/")}`,
    });
  });

  return crumbs;
}

export function FileManager({ apiPrefix, mode, basePath, hiddenNames }: FileManagerProps) {
  const [currentPath, setCurrentPath] = useState("/");
  const [items, setItems] = useState<FileNode[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [listErrorCode, setListErrorCode] = useState<string | null>(null);
  const [sortField, setSortField] = useState<SortField>("name");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [isCreateFolderOpen, setIsCreateFolderOpen] = useState(false);
  const [itemToDelete, setItemToDelete] = useState<FileNode | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isUploadingZip, setIsUploadingZip] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const zipFileInputRef = useRef<HTMLInputElement>(null);
  const [zipUploadProgress, setZipUploadProgress] = useState<{
    name: string;
    progress: number;
    status: "uploading" | "done" | "error";
    files?: number;
    dirs?: number;
    error?: string;
  } | null>(null);

  const { uploads, uploadFiles, clearFinished } = useFileUpload(apiPrefix);
  const fetchQuota = useQuotaStore((state) => state.fetchQuota);

  const root = normalizeBasePath(basePath);
  const hiddenNamesKey = (hiddenNames ?? []).join("|");

  const hiddenNameSet = useMemo(
    () => new Set(hiddenNamesKey ? hiddenNamesKey.split("|") : []),
    [hiddenNamesKey],
  );

  const rootLabel = useMemo(() => {
    const segments = root.split("/").filter(Boolean);
    return segments.length > 0 ? segments[segments.length - 1] : "Root";
  }, [root]);

  const sortedItems = useMemo(
    () => sortItems(items, sortField, sortDirection),
    [items, sortField, sortDirection],
  );

  const breadcrumbs = useMemo(
    () => buildBreadcrumbs(currentPath, rootLabel),
    [currentPath, rootLabel],
  );

  const refreshDirectory = useCallback(async () => {
    setIsLoading(true);
    setListErrorCode(null);

    try {
      const data = await listFiles(apiPrefix, toApiPath(root, currentPath));
      const visibleItems =
        hiddenNameSet.size > 0 ? data.filter((item) => !hiddenNameSet.has(item.name)) : data;

      setItems(
        root
          ? visibleItems.map((item) => ({ ...item, path: toLocalPath(root, item.path) }))
          : visibleItems,
      );
    } catch (error) {
      const detail = getApiErrorDetail(error);
      setListErrorCode(detail?.error_code ?? "INTERNAL_ERROR");
      setItems([]);
    } finally {
      setIsLoading(false);
    }
  }, [apiPrefix, currentPath, hiddenNameSet, root]);

  useEffect(() => {
    void refreshDirectory();
  }, [refreshDirectory]);

  useEffect(() => {
    const hasFinishedUploads = uploads.some(
      (upload) => upload.status === "done" || upload.status === "error",
    );

    if (!hasFinishedUploads) {
      return;
    }

    const hasSuccessfulUpload = uploads.some((upload) => upload.status === "done");

    if (hasSuccessfulUpload) {
      void refreshDirectory();
      void fetchQuota();
    }

    const timer = window.setTimeout(() => {
      clearFinished();
    }, 4000);

    return () => window.clearTimeout(timer);
  }, [uploads, refreshDirectory, fetchQuota, clearFinished]);

  const handleSortChange = (field: SortField) => {
    if (field === sortField) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }

    setSortField(field);
    setSortDirection(field === "name" ? "asc" : "desc");
  };

  const handleUpload = async (files: File[]) => {
    await uploadFiles(files, toApiPath(root, currentPath));
  };

  const handleZipUpload = async (zipFile: File) => {
    setIsUploadingZip(true);
    setZipUploadProgress({
      name: zipFile.name,
      progress: 0,
      status: "uploading",
    });

    try {
      const result = await uploadZipFolder(
        apiPrefix,
        toApiPath(root, currentPath),
        zipFile,
        (progress) => {
          setZipUploadProgress((prev) =>
            prev ? { ...prev, progress } : null,
          );
        },
      );

      setZipUploadProgress((prev) =>
        prev
          ? {
              ...prev,
              progress: 100,
              status: "done",
              files: result.files,
              dirs: result.dirs,
            }
          : null,
      );

      void refreshDirectory();
      void fetchQuota();

      setTimeout(() => {
        setZipUploadProgress(null);
        setIsUploadingZip(false);
      }, 3000);
    } catch (error) {
      const detail = getApiErrorDetail(error);
      setZipUploadProgress((prev) =>
        prev
          ? {
              ...prev,
              status: "error",
              error: detail?.message ?? "Upload failed",
            }
          : null,
      );
      showErrorToast(error);

      setTimeout(() => {
        setZipUploadProgress(null);
        setIsUploadingZip(false);
      }, 5000);
    }
  };

  const handleZipDrop = async (zipFiles: File[]) => {
    for (const zipFile of zipFiles) {
      await handleZipUpload(zipFile);
    }
  };

  const handleCreateFolder = async (name: string) => {
    try {
      await createDirectory(apiPrefix, toApiPath(root, currentPath), name);
      await refreshDirectory();
      showSuccessToast("Folder created");
    } catch (error) {
      showErrorToast(error);
      throw error;
    }
  };

  const handleDownload = async (item: FileNode) => {
    await downloadFile(apiPrefix, toApiPath(root, item.path), item.name);
  };

  const handleDownloadFolder = async (item: FileNode) => {
    await downloadFolder(apiPrefix, toApiPath(root, item.path), item.name);
  };

  const handleRename = async (item: FileNode, newName: string) => {
    await renameEntry(apiPrefix, toApiPath(root, item.path), newName);
    await refreshDirectory();
  };

  const handleDelete = async (item: FileNode) => {
    await deleteFile(apiPrefix, toApiPath(root, item.path));
    await refreshDirectory();
    void fetchQuota();
  };

  const confirmDelete = async () => {
    if (!itemToDelete) {
      return;
    }

    setIsDeleting(true);

    try {
      await handleDelete(itemToDelete);
      setItemToDelete(null);
      showSuccessToast("Deleted");
    } catch (error) {
      showErrorToast(error);
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-zinc-400">
          {mode === "encrypted" && (
            <span className="inline-flex items-center gap-1 rounded-md bg-violet-900/40 px-2 py-1 text-xs text-violet-300">
              <Lock className="h-3.5 w-3.5" aria-hidden="true" />
              Encrypted
            </span>
          )}
          <nav aria-label="Folder navigation" className="flex flex-wrap items-center gap-1">
            {breadcrumbs.map((crumb, index) => (
              <span key={crumb.path} className="inline-flex items-center gap-1">
                {index > 0 && (
                  <ChevronRight className="h-4 w-4 text-zinc-600" aria-hidden="true" />
                )}
                <button
                  type="button"
                  onClick={() => setCurrentPath(crumb.path)}
                  className={[
                    "rounded px-1.5 py-0.5 transition-colors hover:bg-zinc-800 hover:text-zinc-100",
                    crumb.path === currentPath ? "text-zinc-100" : "text-zinc-400",
                  ].join(" ")}
                >
                  {crumb.label}
                </button>
              </span>
            ))}
          </nav>
        </div>

        <div className="flex flex-wrap gap-2">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => {
              const selectedFiles = Array.from(event.target.files ?? []);
              if (selectedFiles.length > 0) {
                void handleUpload(selectedFiles);
              }
              event.target.value = "";
            }}
          />
          <input
            ref={zipFileInputRef}
            type="file"
            accept=".zip"
            className="hidden"
            onChange={(event) => {
              const selectedFile = event.target.files?.[0];
              if (selectedFile) {
                void handleZipUpload(selectedFile);
              }
              event.target.value = "";
            }}
          />
          <Button
            type="button"
            variant="secondary"
            className="w-auto px-3"
            onClick={() => fileInputRef.current?.click()}
          >
            <span className="inline-flex items-center gap-2">
              <Upload className="h-4 w-4" />
              Upload
            </span>
          </Button>
          <Button
            type="button"
            variant="secondary"
            className="w-auto px-3"
            onClick={() => zipFileInputRef.current?.click()}
            disabled={isUploadingZip}
          >
            <span className="inline-flex items-center gap-2">
              <Package className="h-4 w-4" />
              Upload folder
            </span>
          </Button>
          <Button
            type="button"
            variant="secondary"
            className="w-auto px-3"
            onClick={() => setIsCreateFolderOpen(true)}
          >
            <span className="inline-flex items-center gap-2">
              <FolderPlus className="h-4 w-4" />
              New folder
            </span>
          </Button>
        </div>
      </div>

      {listErrorCode && <ErrorMessage errorCode={listErrorCode} />}

      {uploads.length > 0 && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/70 p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-medium text-zinc-200">Uploading files</p>
            <button
              type="button"
              onClick={clearFinished}
              className="rounded p-1 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-300"
              aria-label="Hide completed"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <ul className="space-y-2">
            {uploads.map((upload) => (
              <li key={upload.id} className="text-sm">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="truncate text-zinc-300">{upload.name}</span>
                  <span className="shrink-0 text-xs text-zinc-500">
                    {upload.status === "uploading" &&
                      (upload.progress >= 100 ? "Saving..." : `${upload.progress}%`)}
                    {upload.status === "done" && "Done"}
                    {upload.status === "error" && "Error"}
                  </span>
                </div>
                {upload.status === "uploading" && (
                  <div className="h-1.5 overflow-hidden rounded-full bg-zinc-800">
                    <div
                      className="h-full rounded-full bg-sky-500 transition-all"
                      style={{ width: `${upload.progress}%` }}
                    />
                  </div>
                )}
                {upload.status === "error" && (
                  <ErrorMessage errorCode={upload.error_code} className="text-xs" />
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {zipUploadProgress && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/70 p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-medium text-zinc-200">Uploading folder</p>
            {zipUploadProgress.status === "done" && (
              <button
                type="button"
                onClick={() => setZipUploadProgress(null)}
                className="rounded p-1 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-300"
                aria-label="Hide"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="truncate text-zinc-300">{zipUploadProgress.name}</span>
            <span className="shrink-0 text-xs text-zinc-500">
              {zipUploadProgress.status === "uploading" &&
                (zipUploadProgress.progress >= 100
                  ? "Saving..."
                  : `${zipUploadProgress.progress}%`)}
              {zipUploadProgress.status === "done" && "Done"}
              {zipUploadProgress.status === "error" && "Error"}
            </span>
          </div>
          {zipUploadProgress.status === "uploading" && (
            <div className="h-1.5 overflow-hidden rounded-full bg-zinc-800">
              <div
                className="h-full rounded-full bg-sky-500 transition-all"
                style={{ width: `${zipUploadProgress.progress}%` }}
              />
            </div>
          )}
          {zipUploadProgress.status === "done" && zipUploadProgress.files !== undefined && (
            <p className="mt-2 text-xs text-zinc-400">
              Extracted {zipUploadProgress.files} files into {zipUploadProgress.dirs} folders
            </p>
          )}
          {zipUploadProgress.status === "error" && zipUploadProgress.error && (
            <ErrorMessage errorCode={zipUploadProgress.error} className="text-xs" />
          )}
        </div>
      )}

      <DropZone
        onDrop={(files) => void handleUpload(files)}
        onDropZip={(zipFiles) => void handleZipDrop(zipFiles)}
        disabled={isLoading}
      >
        <FileList
          items={sortedItems}
          isLoading={isLoading}
          showUploader={apiPrefix === "/api/shared"}
          sortField={sortField}
          sortDirection={sortDirection}
          onSortChange={handleSortChange}
          onOpenFolder={setCurrentPath}
          onDownload={handleDownload}
          onDownloadFolder={handleDownloadFolder}
          onRename={handleRename}
          onDeleteRequest={setItemToDelete}
        />
      </DropZone>

      <Modal
        isOpen={itemToDelete !== null}
        onClose={() => {
          if (!isDeleting) {
            setItemToDelete(null);
          }
        }}
        title="Delete?"
      >
        {itemToDelete && (
          <>
            <p className="mb-4 text-sm text-zinc-300">
              {itemToDelete.is_dir
                ? `Folder “${itemToDelete.name}” and all of its contents will be permanently deleted.`
                : `File “${itemToDelete.name}” will be permanently deleted.`}
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="secondary"
                onClick={() => setItemToDelete(null)}
                disabled={isDeleting}
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="danger"
                isLoading={isDeleting}
                onClick={() => {
                  void confirmDelete();
                }}
              >
                Delete
              </Button>
            </div>
          </>
        )}
      </Modal>

      <CreateFolderDialog
        isOpen={isCreateFolderOpen}
        onClose={() => setIsCreateFolderOpen(false)}
        onCreate={handleCreateFolder}
      />
    </div>
  );
}
