import { isAxiosError } from "axios";
import { Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { DeleteGeneratedDialog } from "../components/Generated/DeleteGeneratedDialog";
import { Button } from "../components/ui/Button";
import { ErrorMessage } from "../components/ui/ErrorMessage";
import { getApiErrorDetail } from "../services/api";
import { deleteGenerated, getGenerated } from "../services/generatedApi";
import { useQuotaStore } from "../store/quota";
import type { GeneratedRun } from "../types/generated";
import { formatDateTime } from "../utils/format";
import { formatGeneratedMetaLine, referenceLabel } from "../utils/generated";

export default function GeneratedImagePage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const fetchQuota = useQuotaStore((state) => state.fetchQuota);

  const [run, setRun] = useState<GeneratedRun | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteErrorCode, setDeleteErrorCode] = useState<string | null>(null);

  const loadRun = useCallback(async (runId: string) => {
    setIsLoading(true);
    setNotFound(false);
    setErrorCode(null);
    setRun(null);

    try {
      const data = await getGenerated(runId);
      setRun(data);
    } catch (error) {
      const status = isAxiosError(error) ? error.response?.status : undefined;
      const code = getApiErrorDetail(error)?.error_code ?? null;

      if (status === 404) {
        setNotFound(true);
        return;
      }

      if (status === 409 || code === "GENERATED_META_INVALID") {
        setErrorCode(code ?? "GENERATED_META_INVALID");
        return;
      }

      setErrorCode(code ?? "INTERNAL_ERROR");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!id) {
      setIsLoading(false);
      setNotFound(true);
      return;
    }

    void loadRun(id);
  }, [id, loadRun]);

  const handleConfirmDelete = useCallback(async () => {
    if (!run) {
      return;
    }

    setIsDeleting(true);
    setDeleteErrorCode(null);

    try {
      await deleteGenerated(run.id);
      void fetchQuota();
      navigate("/generated");
    } catch (error) {
      setDeleteErrorCode(getApiErrorDetail(error)?.error_code ?? "INTERNAL_ERROR");
    } finally {
      setIsDeleting(false);
    }
  }, [fetchQuota, navigate, run]);

  if (isLoading) {
    return (
      <div className="flex justify-center py-12" aria-live="polite">
        <span
          className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-600 border-t-sky-500"
          aria-label="Loading"
        />
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="flex flex-col items-start gap-3">
        <h2 className="text-xl font-semibold text-zinc-100">404</h2>
        <p className="text-sm text-zinc-400">This generated image does not exist.</p>
        <Link to="/generated" className="text-sm font-medium text-sky-400 hover:text-sky-300">
          Back to Generated Images
        </Link>
      </div>
    );
  }

  if (errorCode || !run) {
    return (
      <div className="flex flex-col items-start gap-3">
        <ErrorMessage errorCode={errorCode ?? "INTERNAL_ERROR"} />
        <Link to="/generated" className="text-sm font-medium text-sky-400 hover:text-sky-300">
          Back to Generated Images
        </Link>
      </div>
    );
  }

  const label = formatDateTime(run.created_at);
  const negativePrompt = run.negative_prompt.trim();

  return (
    <div className="flex min-h-0 flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to="/generated" className="text-sm text-zinc-500 hover:text-zinc-300">
            Generated Images
          </Link>
          <h2 className="mt-1 text-xl font-semibold text-zinc-100">{label}</h2>
        </div>
        <Button
          type="button"
          variant="danger"
          className="w-auto px-3"
          onClick={() => {
            setDeleteErrorCode(null);
            setIsDeleteOpen(true);
          }}
        >
          <span className="inline-flex items-center gap-2">
            <Trash2 className="h-4 w-4" />
            Delete
          </span>
        </Button>
      </div>

      <section className="flex flex-col gap-2">
        <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Prompt</h3>
        <p className="whitespace-pre-wrap text-sm text-zinc-100">{run.prompt}</p>
      </section>

      {negativePrompt !== "" && (
        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">
            Negative prompt
          </h3>
          <p className="whitespace-pre-wrap text-sm text-zinc-300">{negativePrompt}</p>
        </section>
      )}

      {run.references.length > 0 && (
        <section className="flex flex-col gap-3">
          <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">
            References
          </h3>
          <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {run.references.map((url, index) => {
              const caption = referenceLabel(index);

              return (
                <li key={`${url}-${index}`} className="flex flex-col gap-2">
                  <img
                    src={url}
                    alt={caption}
                    className="aspect-square w-full rounded-lg border border-zinc-800 bg-zinc-900 object-contain"
                  />
                  <p className="text-sm text-zinc-400">{caption}</p>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section className="flex flex-col gap-3">
        <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Result</h3>
        <img
          src={run.result}
          alt="Result"
          className="max-h-[70vh] w-full max-w-3xl rounded-lg border border-zinc-800 bg-zinc-900 object-contain"
        />
        <p className="text-sm text-zinc-400">{formatGeneratedMetaLine(run)}</p>
      </section>

      <DeleteGeneratedDialog
        isOpen={isDeleteOpen}
        createdAt={run.created_at}
        isDeleting={isDeleting}
        errorCode={deleteErrorCode}
        onClose={() => {
          if (!isDeleting) {
            setIsDeleteOpen(false);
            setDeleteErrorCode(null);
          }
        }}
        onConfirm={() => {
          void handleConfirmDelete();
        }}
      />
    </div>
  );
}
