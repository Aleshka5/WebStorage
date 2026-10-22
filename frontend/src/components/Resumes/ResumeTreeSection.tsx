import { FormEvent, useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Pencil, Plus, Trash2 } from "lucide-react";
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
import { LoadingBlock } from "../ui/LoadingBlock";
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
    <Button type="button" variant="secondary" onClick={() => setIsAddOpen(true)}>
      <Plus size={16} aria-hidden="true" />
      {addLabel}
    </Button>
  );

  if (isLoading) {
    return <LoadingBlock active label={`Loading ${entityLabel}s…`} />;
  }

  if (loadErrorCode === "FILE_NOT_FOUND") {
    return (
      <div className="error-block">
        <ErrorMessage errorCode="FILE_NOT_FOUND" message="This entry no longer exists" />
        <Link className="btn" to="/resumes">
          Back to Resumes
        </Link>
      </div>
    );
  }

  if (loadErrorCode) {
    return (
      <div className="error-block">
        <ErrorMessage errorCode={loadErrorCode} />
      </div>
    );
  }

  return (
    <div className="tool">
      {toolbar}

      {items.length === 0 ? (
        <div className="empty">
          <p>{emptyMessage}</p>
          {addButton}
        </div>
      ) : (
        <div className="tool">
          <ul className="choice-list">
            {items.map((item, index) => (
              <li key={item.path} className="choice choice-with-actions">
                <Link to={buildItemHref(item)}>
                  <span className="choice-num">{String(index + 1).padStart(2, "0")}</span>
                  <span className="choice-body">
                    <span className="choice-title">{item.name}</span>
                    {renderItemMeta?.(item)}
                  </span>
                  <span className="choice-arrow" aria-hidden="true">
                    →
                  </span>
                </Link>
                <div className="cell-actions">
                  <button
                    type="button"
                    title="Rename"
                    aria-label={`Rename ${item.name}`}
                    className="icon-btn"
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
                    className="icon-btn danger"
                    onClick={() => setDeleteTarget(item)}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
          <div>{addButton}</div>
        </div>
      )}

      <Modal isOpen={isAddOpen} onClose={closeAddDialog} title={addLabel}>
        <form className="stack" onSubmit={handleCreate}>
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
            <div className="field">
              <label htmlFor="new-vacancy-status">Status</label>
              <select
                id="new-vacancy-status"
                value={newStatusId}
                onChange={(event) => setNewStatusId(event.target.value)}
                disabled={isCreating}
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
          {addError && <p className="form-error">{addError}</p>}
          <div className="actions">
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
        <form className="stack" onSubmit={handleRename}>
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
          {renameError && <p className="form-error">{renameError}</p>}
          <div className="actions">
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
            <p>
              {`“${deleteTarget.name}” will be permanently deleted along with ${deleteTarget.child_count} ${
                deleteTarget.child_count === 1 ? childLabelSingular : childLabelPlural
              } and every file inside.`}
            </p>
            <div className="actions">
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
