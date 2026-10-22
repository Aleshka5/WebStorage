import { isAxiosError } from "axios";
import { Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { DeleteGeneratedDialog } from "../components/Generated/DeleteGeneratedDialog";
import { PageFrame } from "../components/Layout/PageFrame";
import { Button } from "../components/ui/Button";
import { ErrorMessage } from "../components/ui/ErrorMessage";
import { LoadingBlock } from "../components/ui/LoadingBlock";
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
      <PageFrame title="Generated image" lead="One saved image and the prompt that made it.">
        <LoadingBlock active label="Loading generated image…" />
      </PageFrame>
    );
  }

  if (notFound) {
    return (
      <PageFrame title="Missing image">
        <div className="error-block">
          <p>This generated image does not exist.</p>
          <Link className="btn" to="/generated">
            Back to Generated
          </Link>
        </div>
      </PageFrame>
    );
  }

  if (errorCode || !run) {
    return (
      <PageFrame title="Generated image">
        <div className="error-block">
          <ErrorMessage errorCode={errorCode ?? "INTERNAL_ERROR"} />
          <Link className="btn" to="/generated">
            Back to Generated
          </Link>
        </div>
      </PageFrame>
    );
  }

  const label = formatDateTime(run.created_at);
  const negativePrompt = run.negative_prompt.trim();

  return (
    <PageFrame
      title={label}
      kicker="Generated"
      actions={
        <Button
          type="button"
          variant="danger"
          onClick={() => {
            setDeleteErrorCode(null);
            setIsDeleteOpen(true);
          }}
        >
          <Trash2 size={16} aria-hidden="true" />
          Delete
        </Button>
      }
    >
      <Link className="up" to="/generated">
        Generated
      </Link>

      <section className="a-section">
        <h2>Prompt</h2>
        <p className="prewrap">{run.prompt}</p>
      </section>

      {negativePrompt !== "" && (
        <section className="a-section">
          <h2>Negative prompt</h2>
          <p className="prewrap">{negativePrompt}</p>
        </section>
      )}

      {run.references.length > 0 && (
        <section className="a-section">
          <h2>References</h2>
          <ul className="figure-grid">
            {run.references.map((url, index) => {
              const caption = referenceLabel(index);

              return (
                <li key={`${url}-${index}`}>
                  <figure className="a-figure">
                    <img src={url} alt={caption} width={400} height={400} loading="lazy" decoding="async" />
                    <figcaption>{caption}</figcaption>
                  </figure>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section className="a-section">
        <h2>Result</h2>
        <figure className="a-figure">
          <img src={run.result} alt="Result" width={1200} height={800} loading="lazy" decoding="async" />
          <figcaption>{formatGeneratedMetaLine(run)}</figcaption>
        </figure>
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
    </PageFrame>
  );
}
