import { FormEvent, useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronRight, Pencil, Plus, Trash2 } from "lucide-react";
import { getApiErrorDetail } from "../../services/api";
import {
  createResumeNode,
  deleteResumeNode,
  listResumeTree,
  renameResumeNode,
} from "../../services/resumesApi";
import type { ResumeNode, ResumeStatus } from "../../types/resumes";
import { isInlineResumeErrorCode, validateResumeNodeName } from "../../utils/resumes";
import { formatApiError, showErrorToast, showSuccessToast } from "../../utils/toast";
import { Button } from "../ui/Button";
import { ErrorMessage } from "../ui/ErrorMessage";
import { Input } from "../ui/Input";
import { Modal } from "../ui/Modal";

interface ResumeTreeSectionProps {
  parentPath: string;
  entityLabel: string;
  addLabel: string;
  nameLabel: string;
  emptyMessage: string;
  childLabelSingular: string;
  childLabelPlural: string;
  buildItemHref: (node: ResumeNode) => string;
  statuses?: ResumeStatus[];
  toolbar?: ReactNode;
  renderItemMeta?: (node: ResumeNode) => ReactNode;
}

export function ResumeTreeSection({
  parentPath,
  entityLabel,
  addLabel,
  nameLabel,
  emptyMessage,
  childLabelSingular,
  childLabelPlural,
  buildItemHref,
  statuses,
  toolbar,
  renderItemMeta,
}: ResumeTreeSectionProps) {
  const [items, setItems] = useState<ResumeNode[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadErrorCode, setLoadErrorCode] = useState<string | null>(null);
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newStatusId, setNewStatusId] = useState("");
  const [addError, setAddError] = useState<string | undefined>();
  const [isCreating, setIsCreating] = useState(false);
  const [renameTarget, setRenameTarget] = useState<ResumeNode | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameError, setRenameError] = useState<string | undefined>();
  const [isRenaming, setIsRenaming] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ResumeNode | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const loadItems = useCallback(async () => {
    setIsLoading(true);
    setLoadErrorCode(null);

    try {
      const data = await listResumeTree(parentPath);
      setItems(data.items);
    } catch (error) {
      const detail = getApiErrorDetail(error);
      setLoadErrorCode(detail?.error_code ?? "INTERNAL_ERROR");
      setItems([]);
    } finally {
      setIsLoading(false);
    }
  }, [parentPath]);

  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  const closeAddDialog = useCallback(() => {
    setIsAddOpen(false);
    setNewName("");
    setNewStatusId("");
    setAddError(undefined);
    setIsCreating(false);
  }, []);

  const handleCreate = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();

      const name = newName.trim();
      const validationError = validateResumeNodeName(name);

      if (validationError) {
        setAddError(validationError);
        return;
      }

      if (items.some((item) => item.name.toLowerCase() === name.toLowerCase())) {
        setAddError(`A ${entityLabel} with this name already exists`);
        return;
      }

      setIsCreating(true);
      setAddError(undefined);

      try {
        await createResumeNode(parentPath, name, statuses ? newStatusId || null : undefined);
        await loadItems();
        showSuccessToast(
          `${entityLabel.charAt(0).toUpperCase()}${entityLabel.slice(1)} created`,
        );
        closeAddDialog();
      } catch (error) {
        const detail = getApiErrorDetail(error);
        setAddError(formatApiError(detail));
        if (!isInlineResumeErrorCode(detail?.error_code)) {
          showErrorToast(error);
        }
        setIsCreating(false);
      }
    },
    [closeAddDialog, entityLabel, items, loadItems, newName, newStatusId, parentPath, statuses],
  );

  const closeRenameDialog = useCallback(() => {
    setRenameTarget(null);
    setRenameValue("");
    setRenameError(undefined);
    setIsRenaming(false);
  }, []);

  const handleRename = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();

      if (!renameTarget) {
        return;
      }

      const name = renameValue.trim();
      const validationError = validateResumeNodeName(name);

      if (validationError) {
        setRenameError(validationError);
        return;
      }

      if (name === renameTarget.name) {
        closeRenameDialog();
        return;
      }

      if (items.some((item) => item.name.toLowerCase() === name.toLowerCase())) {
        setRenameError(`A ${entityLabel} with this name already exists`);
        return;
      }

      setIsRenaming(true);
      setRenameError(undefined);

      try {
        await renameResumeNode(renameTarget.path, name);
        await loadItems();
        showSuccessToast("Renamed");
        closeRenameDialog();
      } catch (error) {
        const detail = getApiErrorDetail(error);
        setRenameError(formatApiError(detail));
        if (!isInlineResumeErrorCode(detail?.error_code)) {
          showErrorToast(error);
        }
        setIsRenaming(false);
      }
    },
    [closeRenameDialog, entityLabel, items, loadItems, renameTarget, renameValue],
  );

  const handleDelete = useCallback(async () => {
    if (!deleteTarget) {
      return;
    }

    setIsDeleting(true);

    try {
      await deleteResumeNode(deleteTarget.path);
      await loadItems();
      setDeleteTarget(null);
      showSuccessToast("Deleted");
    } catch (error) {
      showErrorToast(error);
    } finally {
      setIsDeleting(false);
    }
  }, [deleteTarget, loadItems]);

  const addButton = (
    <Button type="button" variant="secondary" className="w-auto" onClick={() => setIsAddOpen(true)}>
      <Plus size={16} className="mr-1.5" />
      {addLabel}
    </Button>
  );

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <span className="h-8 w-8 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />
      </div>
    );
  }

  if (loadErrorCode === "FILE_NOT_FOUND") {
    return (
      <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-zinc-700 px-6 py-16 text-center">
        <ErrorMessage errorCode="FILE_NOT_FOUND" message="This entry no longer exists" />
        <Link
          to="/resumes"
          className="rounded-lg bg-zinc-700 px-4 py-2.5 text-sm font-medium text-zinc-100 transition-colors hover:bg-zinc-600"
        >
          Back to Resumes
        </Link>
      </div>
    );
  }

  if (loadErrorCode) {
    return <ErrorMessage errorCode={loadErrorCode} />;
  }

  return (
    <div className="flex min-h-0 flex-col gap-4">
      {toolbar}

      {items.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-zinc-700 px-6 py-16 text-center">
          {addButton}
          <p className="text-sm text-zinc-400">{emptyMessage}</p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {items.length === 0 ? (
            <p className="rounded-lg border border-dashed border-zinc-700 px-4 py-8 text-center text-sm text-zinc-400">
              Nothing matches the current filter
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {items.map((item) => (
                <li
                  key={item.path}
                  className="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2.5"
                >
                  <Link
                    to={buildItemHref(item)}
                    className="flex min-w-0 flex-1 items-center gap-3 rounded-md px-1 py-1 transition-colors hover:bg-zinc-800/70"
                  >
                    <span className="truncate text-sm font-medium text-zinc-100">{item.name}</span>
                    {renderItemMeta?.(item)}
                    <ChevronRight className="ml-auto h-4 w-4 shrink-0 text-zinc-600" aria-hidden="true" />
                  </Link>
                  <button
                    type="button"
                    title="Rename"
                    aria-label={`Rename ${item.name}`}
                    className="rounded-lg p-2 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100"
                    onClick={() => {
                      setRenameTarget(item);
                      setRenameValue(item.name);
                      setRenameError(undefined);
                    }}
                  >
                    <Pencil size={16} />
                  </button>
                  <button
                    type="button"
                    title="Delete"
                    aria-label={`Delete ${item.name}`}
                    className="rounded-lg p-2 text-zinc-400 transition-colors hover:bg-red-500/10 hover:text-red-400"
                    onClick={() => setDeleteTarget(item)}
                  >
                    <Trash2 size={16} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex">{addButton}</div>
        </div>
      )}

      <Modal isOpen={isAddOpen} onClose={closeAddDialog} title={addLabel}>
        <form className="flex flex-col gap-4" onSubmit={handleCreate}>
          <Input
            label={nameLabel}
            value={newName}
            onChange={(event) => {
              setNewName(event.target.value);
              setAddError(undefined);
            }}
            autoComplete="off"
            autoFocus
            disabled={isCreating}
          />
          {statuses && (
            <div className="flex flex-col gap-1.5">
              <label htmlFor="new-vacancy-status" className="text-sm font-medium text-zinc-300">
                Status
              </label>
              <select
                id="new-vacancy-status"
                value={newStatusId}
                onChange={(event) => setNewStatusId(event.target.value)}
                disabled={isCreating}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-800/80 px-3 py-2.5 text-sm text-zinc-100 transition-colors hover:border-zinc-600 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
              >
                <option value="">No status</option>
                {statuses.map((status) => (
                  <option key={status.id} value={status.id}>
                    {status.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          {addError && <p className="text-xs text-red-400">{addError}</p>}
          <div className="flex gap-2">
            <Button type="button" variant="secondary" onClick={closeAddDialog} disabled={isCreating}>
              Cancel
            </Button>
            <Button type="submit" isLoading={isCreating}>
              Create
            </Button>
          </div>
        </form>
      </Modal>

      <Modal
        isOpen={renameTarget !== null}
        onClose={() => {
          if (!isRenaming) {
            closeRenameDialog();
          }
        }}
        title={`Rename ${entityLabel}`}
      >
        <form className="flex flex-col gap-4" onSubmit={handleRename}>
          <Input
            label={nameLabel}
            value={renameValue}
            onChange={(event) => {
              setRenameValue(event.target.value);
              setRenameError(undefined);
            }}
            autoComplete="off"
            autoFocus
            disabled={isRenaming}
          />
          {renameError && <p className="text-xs text-red-400">{renameError}</p>}
          <div className="flex gap-2">
            <Button
              type="button"
              variant="secondary"
              onClick={closeRenameDialog}
              disabled={isRenaming}
            >
              Cancel
            </Button>
            <Button type="submit" isLoading={isRenaming}>
              Rename
            </Button>
          </div>
        </form>
      </Modal>

      <Modal
        isOpen={deleteTarget !== null}
        onClose={() => {
          if (!isDeleting) {
            setDeleteTarget(null);
          }
        }}
        title="Delete?"
      >
        {deleteTarget && (
          <>
            <p className="mb-4 text-sm text-zinc-300">
              {`“${deleteTarget.name}” will be permanently deleted along with ${deleteTarget.child_count} ${
                deleteTarget.child_count === 1 ? childLabelSingular : childLabelPlural
              } and every file inside.`}
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="secondary"
                onClick={() => setDeleteTarget(null)}
                disabled={isDeleting}
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="danger"
                isLoading={isDeleting}
                onClick={() => {
                  void handleDelete();
                }}
              >
                Delete
              </Button>
            </div>
          </>
        )}
      </Modal>
    </div>
  );
}
