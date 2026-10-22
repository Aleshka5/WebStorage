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
  showUploader?: boolean;
  onOpenFolder: (path: string) => void;
  onDownload: (item: FileNode) => Promise<void>;
  onRename: (item: FileNode, newName: string) => Promise<void>;
  onDeleteRequest: (item: FileNode) => void;
  onDownloadFolder: (item: FileNode) => Promise<void>;
}

export function FileItem({
  item,
  showUploader = false,
  onOpenFolder,
  onDownload,
  onDownloadFolder,
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
      setActionError("Failed to rename");
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
      setActionError("Failed to download file");
    } finally {
      setIsBusy(false);
    }
  };

  const handleDownloadFolder = async () => {
    setIsBusy(true);
    setActionError(undefined);

    try {
      await onDownloadFolder(item);
    } catch {
      setActionError("Failed to download folder");
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <tr onDoubleClick={handleDoubleClick}>
      <td>
        {item.is_dir ? (
          <Folder size={18} aria-hidden="true" />
        ) : (
          <File size={18} aria-hidden="true" />
        )}
      </td>
      <td>
        {isRenaming ? (
          <div className={renameError ? "field is-invalid" : "field"}>
            <label className="sr-only" htmlFor={`rename-${item.path}`}>
              New name
            </label>
            <input
              id={`rename-${item.path}`}
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
            />
            {renameError && <p className="form-error">{renameError}</p>}
          </div>
        ) : (
          <div>
            <button
              type="button"
              className="name-button"
              onClick={() => {
                if (item.is_dir) {
                  onOpenFolder(item.path);
                }
              }}
            >
              {item.name}
            </button>
            {showUploader && !item.is_dir && item.uploaded_by && (
              <p className="uploader">{item.uploaded_by}</p>
            )}
          </div>
        )}
      </td>
      <td className="hide-sm">{formatBytes(item.size, item.is_dir)}</td>
      <td className="hide-md">{formatDateTime(item.modified_at)}</td>
      <td>
        <div className="cell-actions">
          {item.is_dir ? (
            <button
              type="button"
              title="Download as archive"
              aria-label="Download as archive"
              disabled={isBusy}
              onClick={() => {
                void handleDownloadFolder();
              }}
              className="icon-btn"
            >
              <Download size={16} />
            </button>
          ) : (
            <button
              type="button"
              title="Download"
              aria-label="Download"
              disabled={isBusy}
              onClick={() => {
                void handleDownload();
              }}
              className="icon-btn"
            >
              <Download size={16} />
            </button>
          )}
          <button
            type="button"
            title="Rename"
            aria-label="Rename"
            disabled={isBusy || isRenaming}
            onClick={startRename}
            className="icon-btn"
          >
            <Pencil size={16} />
          </button>
          <button
            type="button"
            title="Delete"
            aria-label="Delete"
            disabled={isBusy}
            onClick={() => onDeleteRequest(item)}
            className="icon-btn danger"
          >
            <Trash2 size={16} />
          </button>
        </div>
        {actionError && <ErrorMessage message={actionError} />}
      </td>
    </tr>
  );
}
