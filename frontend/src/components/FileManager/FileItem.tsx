import {
  Download,
  File,
  Folder,
  Pencil,
  Trash2,
} from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ErrorMessage } from "../ui/ErrorMessage";
import type { FileNode } from "../../types/files";
import { formatBytes, formatDateTime } from "../../utils/format";
import { validateFileName } from "../../utils/validation";

interface FileItemProps {
  item: FileNode;
  onOpenFolder: (path: string) => void;
  onDownload: (item: FileNode) => Promise<void>;
  onRename: (item: FileNode, newName: string) => Promise<void>;
  onDeleteRequest: (item: FileNode) => void;
}

export function FileItem({
  item,
  onOpenFolder,
  onDownload,
  onRename,
  onDeleteRequest,
}: FileItemProps) {
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(item.name);
  const [renameError, setRenameError] = useState<string | undefined>();
  const [isBusy, setIsBusy] = useState(false);
  const [actionError, setActionError] = useState<string | undefined>();
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isRenaming) {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    }
  }, [isRenaming]);

  const handleDoubleClick = () => {
    if (item.is_dir) {
      onOpenFolder(item.path);
    }
  };

  const startRename = () => {
    setRenameValue(item.name);
    setRenameError(undefined);
    setActionError(undefined);
    setIsRenaming(true);
  };

  const cancelRename = () => {
    setRenameValue(item.name);
    setRenameError(undefined);
    setIsRenaming(false);
  };

  const submitRename = async () => {
    const validationError = validateFileName(renameValue);

    if (validationError) {
      setRenameError(validationError);
      return;
    }

    const trimmedName = renameValue.trim();

    if (trimmedName === item.name) {
      setIsRenaming(false);
      return;
    }

    setIsBusy(true);
    setRenameError(undefined);
    setActionError(undefined);

    try {
      await onRename(item, trimmedName);
      setIsRenaming(false);
    } catch {
      setActionError("Не удалось переименовать");
    } finally {
      setIsBusy(false);
    }
  };

  const handleRenameKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void submitRename();
    }

    if (event.key === "Escape") {
      event.preventDefault();
      cancelRename();
    }
  };

  const handleDownload = async () => {
    setIsBusy(true);
    setActionError(undefined);

    try {
      await onDownload(item);
    } catch {
      setActionError("Не удалось скачать файл");
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <tr
      className="border-b border-zinc-800/80 transition-colors hover:bg-zinc-800/40"
      onDoubleClick={handleDoubleClick}
    >
        <td className="w-10 px-3 py-3 text-zinc-400">
          {item.is_dir ? (
            <Folder className="h-5 w-5 text-amber-400" aria-hidden="true" />
          ) : (
            <File className="h-5 w-5 text-sky-400" aria-hidden="true" />
          )}
        </td>
        <td className="min-w-[180px] px-3 py-3">
          {isRenaming ? (
            <div className="flex flex-col gap-1">
              <input
                ref={renameInputRef}
                value={renameValue}
                onChange={(event) => {
                  setRenameValue(event.target.value);
                  if (renameError) {
                    setRenameError(undefined);
                  }
                }}
                onKeyDown={handleRenameKeyDown}
                onBlur={() => {
                  void submitRename();
                }}
                disabled={isBusy}
                className="w-full rounded-md border border-zinc-600 bg-zinc-800 px-2 py-1 text-sm text-zinc-100 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500/50"
              />
              {renameError && <p className="text-xs text-red-400">{renameError}</p>}
            </div>
          ) : (
            <button
              type="button"
              className="max-w-full truncate text-left text-sm text-zinc-100 hover:text-sky-300"
              onClick={() => {
                if (item.is_dir) {
                  onOpenFolder(item.path);
                }
              }}
            >
              {item.name}
            </button>
          )}
        </td>
        <td className="hidden px-3 py-3 text-sm text-zinc-400 sm:table-cell">
          {formatBytes(item.size, item.is_dir)}
        </td>
        <td className="hidden px-3 py-3 text-sm text-zinc-400 md:table-cell">
          {formatDateTime(item.modified_at)}
        </td>
        <td className="px-3 py-3">
          <div className="flex items-center justify-end gap-1">
            {!item.is_dir && (
              <button
                type="button"
                title="Скачать"
                disabled={isBusy}
                onClick={() => {
                  void handleDownload();
                }}
                className="rounded-md p-2 text-zinc-400 transition-colors hover:bg-zinc-700 hover:text-zinc-100 disabled:opacity-50"
              >
                <Download className="h-4 w-4" />
              </button>
            )}
            <button
              type="button"
              title="Переименовать"
              disabled={isBusy || isRenaming}
              onClick={startRename}
              className="rounded-md p-2 text-zinc-400 transition-colors hover:bg-zinc-700 hover:text-zinc-100 disabled:opacity-50"
            >
              <Pencil className="h-4 w-4" />
            </button>
            <button
              type="button"
              title="Удалить"
              disabled={isBusy}
              onClick={() => onDeleteRequest(item)}
              className="rounded-md p-2 text-zinc-400 transition-colors hover:bg-red-900/40 hover:text-red-300 disabled:opacity-50"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
          {actionError && (
            <ErrorMessage message={actionError} className="mt-1 text-right text-xs" />
          )}
        </td>
      </tr>
  );
}
