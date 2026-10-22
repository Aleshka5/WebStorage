import { Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { DeleteGeneratedDialog } from "../components/Generated/DeleteGeneratedDialog";
import { PageFrame } from "../components/Layout/PageFrame";
import { ErrorMessage } from "../components/ui/ErrorMessage";
import { LoadingBlock } from "../components/ui/LoadingBlock";
import { getApiErrorDetail } from "../services/api";
import { deleteGenerated, listGenerated } from "../services/generatedApi";
import { useQuotaStore } from "../store/quota";
import type { GeneratedListItem } from "../types/generated";
import { formatDateTime } from "../utils/format";

export default function GeneratedImagesPage() {
  const [items, setItems] = useState<GeneratedListItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [listErrorCode, setListErrorCode] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<GeneratedListItem | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteErrorCode, setDeleteErrorCode] = useState<string | null>(null);
  const fetchQuota = useQuotaStore((state) => state.fetchQuota);

  const loadItems = useCallback(async () => {
    setIsLoading(true);
    setListErrorCode(null);

    try {
      const data = await listGenerated();
      setItems(data.items);
    } catch (error) {
      setListErrorCode(getApiErrorDetail(error)?.error_code ?? "INTERNAL_ERROR");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  const handleRequestDelete = useCallback((item: GeneratedListItem) => {
    setDeleteErrorCode(null);
    setPendingDelete(item);
  }, []);

  const handleCloseDelete = useCallback(() => {
    if (isDeleting) {
      return;
    }

    setPendingDelete(null);
    setDeleteErrorCode(null);
  }, [isDeleting]);

  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDelete) {
      return;
    }

    const id = pendingDelete.id;
    setIsDeleting(true);
    setDeleteErrorCode(null);

    try {
      await deleteGenerated(id);
      setItems((current) => current.filter((item) => item.id !== id));
      setPendingDelete(null);
      void fetchQuota();
    } catch (error) {
      setDeleteErrorCode(getApiErrorDetail(error)?.error_code ?? "INTERNAL_ERROR");
    } finally {
      setIsDeleting(false);
    }
  }, [fetchQuota, pendingDelete]);

  return (
    <PageFrame title="Generated" lead="Images made for this account.">
      {listErrorCode && (
        <div className="error-block">
          <ErrorMessage errorCode={listErrorCode} />
          <button type="button" className="btn" onClick={() => void loadItems()}>
            Reload
          </button>
        </div>
      )}

      {isLoading ? (
        <LoadingBlock active label="Loading generated images…" />
      ) : items.length === 0 && !listErrorCode ? (
        <p className="empty">No generated images yet.</p>
      ) : (
        <ul className="choice-list">
          {items.map((item, index) => {
            const label = formatDateTime(item.created_at);
            const number = String(index + 1).padStart(2, "0");

            return (
              <li key={item.id} className="choice choice-with-actions">
                <Link to={`/generated/${item.id}`}>
                  <span className="choice-num">{number}</span>
                  <span className="choice-body">
                    <span className="choice-title">{label}</span>
                    <span className="choice-teaser">{item.prompt}</span>
                  </span>
                  <span className="choice-arrow" aria-hidden="true">
                    →
                  </span>
                </Link>
                <button
                  type="button"
                  title="Delete"
                  aria-label={`Delete ${label}`}
                  onClick={() => handleRequestDelete(item)}
                  className="icon-btn danger"
                >
                  <Trash2 size={16} />
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <DeleteGeneratedDialog
        isOpen={pendingDelete !== null}
        createdAt={pendingDelete?.created_at ?? ""}
        isDeleting={isDeleting}
        errorCode={deleteErrorCode}
        onClose={handleCloseDelete}
        onConfirm={() => {
          void handleConfirmDelete();
        }}
      />
    </PageFrame>
  );
}
