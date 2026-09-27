import { Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { DeleteGeneratedDialog } from "../components/Generated/DeleteGeneratedDialog";
import { ErrorMessage } from "../components/ui/ErrorMessage";
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
    <div className="flex min-h-0 flex-col gap-4">
      <h2 className="text-xl font-semibold text-zinc-100">Generated Images</h2>

      {listErrorCode && <ErrorMessage errorCode={listErrorCode} />}

      {isLoading ? (
        <div className="flex justify-center py-12" aria-live="polite">
          <span
            className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-600 border-t-sky-500"
            aria-label="Loading"
          />
        </div>
      ) : items.length === 0 && !listErrorCode ? (
        <p className="py-12 text-center text-zinc-500">No generated images yet.</p>
      ) : (
        <ul className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/50">
          {items.map((item) => {
            const label = formatDateTime(item.created_at);

            return (
              <li
                key={item.id}
                className="flex items-center gap-2 border-b border-zinc-800/80 last:border-b-0"
              >
                <Link
                  to={`/generated/${item.id}`}
                  className="min-w-0 flex-1 px-4 py-3 transition-colors hover:bg-zinc-800/40"
                >
                  <p className="text-sm font-medium text-zinc-100">{label}</p>
                  <p className="truncate text-sm text-zinc-400">{item.prompt}</p>
                </Link>
                <button
                  type="button"
                  title="Delete"
                  aria-label={`Delete ${label}`}
                  onClick={() => handleRequestDelete(item)}
                  className="mr-2 rounded-md p-2 text-zinc-400 transition-colors hover:bg-red-900/40 hover:text-red-300"
                >
                  <Trash2 className="h-4 w-4" />
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
    </div>
  );
}
