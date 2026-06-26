import {
  ChevronRight,
  FolderPlus,
  Lock,
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
  listFiles,
  renameEntry,
} from "../../services/filesApi";
import { useQuotaStore } from "../../store/quota";
import type { FileManagerMode, FileNode, SortDirection, SortField } from "../../types/files";
import { ErrorMessage } from "../ui/ErrorMessage";
import { Button } from "../ui/Button";
import { Modal } from "../ui/Modal";
import { CreateFolderDialog } from "./CreateFolderDialog";
import { DropZone } from "./DropZone";
import { FileList } from "./FileList";

interface FileManagerProps {
  apiPrefix: string;
  mode: FileManagerMode;
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
      comparison = left.name.localeCompare(right.name, "ru");
    } else if (sortField === "size") {
      comparison = left.size - right.size;
    } else {
      comparison =
        new Date(left.modified_at).getTime() - new Date(right.modified_at).getTime();
    }

    return comparison * directionMultiplier;
  });
}

function buildBreadcrumbs(currentPath: string): Array<{ label: string; path: string }> {
  const normalized = currentPath.replace(/\\/g, "/").replace(/\/+$/, "") || "/";

  if (normalized === "/") {
    return [{ label: "Корень", path: "/" }];
  }

  const segments = normalized.split("/").filter(Boolean);
  const crumbs: Array<{ label: string; path: string }> = [{ label: "Корень", path: "/" }];

  segments.forEach((segment, index) => {
    crumbs.push({
      label: segment,
      path: `/${segments.slice(0, index + 1).join("/")}`,
    });
  });

  return crumbs;
}

export function FileManager({ apiPrefix, mode }: FileManagerProps) {
  const [currentPath, setCurrentPath] = useState("/");
  const [items, setItems] = useState<FileNode[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [listErrorCode, setListErrorCode] = useState<string | null>(null);
  const [sortField, setSortField] = useState<SortField>("name");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [isCreateFolderOpen, setIsCreateFolderOpen] = useState(false);
  const [itemToDelete, setItemToDelete] = useState<FileNode | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { uploads, uploadFiles, clearFinished } = useFileUpload(apiPrefix);
  const fetchQuota = useQuotaStore((state) => state.fetchQuota);

  const sortedItems = useMemo(
    () => sortItems(items, sortField, sortDirection),
    [items, sortField, sortDirection],
  );

  const breadcrumbs = useMemo(() => buildBreadcrumbs(currentPath), [currentPath]);

  const refreshDirectory = useCallback(async () => {
    setIsLoading(true);
    setListErrorCode(null);

    try {
      const data = await listFiles(apiPrefix, currentPath);
      setItems(data);
    } catch (error) {
      const detail = getApiErrorDetail(error);
      setListErrorCode(detail?.error_code ?? "INTERNAL_ERROR");
      setItems([]);
    } finally {
      setIsLoading(false);
    }
  }, [apiPrefix, currentPath]);

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
    await uploadFiles(files, currentPath);
  };

  const handleCreateFolder = async (name: string) => {
    await createDirectory(apiPrefix, currentPath, name);
    await refreshDirectory();
  };

  const handleDownload = async (item: FileNode) => {
    await downloadFile(apiPrefix, item.path, item.name);
  };

  const handleRename = async (item: FileNode, newName: string) => {
    await renameEntry(apiPrefix, item.path, newName);
    await refreshDirectory();
  };

  const handleDelete = async (item: FileNode) => {
    await deleteFile(apiPrefix, item.path);
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
              Зашифровано
            </span>
          )}
          <nav aria-label="Навигация по папкам" className="flex flex-wrap items-center gap-1">
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
          <Button
            type="button"
            variant="secondary"
            className="w-auto px-3"
            onClick={() => fileInputRef.current?.click()}
          >
            <span className="inline-flex items-center gap-2">
              <Upload className="h-4 w-4" />
              Загрузить
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
              Новая папка
            </span>
          </Button>
        </div>
      </div>

      {listErrorCode && <ErrorMessage errorCode={listErrorCode} />}

      {uploads.length > 0 && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/70 p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-medium text-zinc-200">Загрузка файлов</p>
            <button
              type="button"
              onClick={clearFinished}
              className="rounded p-1 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-300"
              aria-label="Скрыть завершённые"
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
                    {upload.status === "uploading" && `${upload.progress}%`}
                    {upload.status === "done" && "Готово"}
                    {upload.status === "error" && "Ошибка"}
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

      <DropZone onDrop={(files) => void handleUpload(files)} disabled={isLoading}>
        <FileList
          items={sortedItems}
          isLoading={isLoading}
          showUploader={apiPrefix === "/api/shared"}
          sortField={sortField}
          sortDirection={sortDirection}
          onSortChange={handleSortChange}
          onOpenFolder={setCurrentPath}
          onDownload={handleDownload}
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
        title="Удалить?"
      >
        {itemToDelete && (
          <>
            <p className="mb-4 text-sm text-zinc-300">
              {itemToDelete.is_dir
                ? `Папка «${itemToDelete.name}» и всё её содержимое будут удалены без возможности восстановления.`
                : `Файл «${itemToDelete.name}» будет удалён без возможности восстановления.`}
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="secondary"
                onClick={() => setItemToDelete(null)}
                disabled={isDeleting}
              >
                Отмена
              </Button>
              <Button
                type="button"
                variant="danger"
                isLoading={isDeleting}
                onClick={() => {
                  void confirmDelete();
                }}
              >
                Удалить
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
