import { useCallback, useEffect, useMemo, useState } from "react";
import { useBlocker, useParams } from "react-router-dom";
import { ExternalLink, Plus, Trash2 } from "lucide-react";
import { FileManager } from "../components/FileManager/FileManager";
import { PageFrame } from "../components/Layout/PageFrame";
import { ResumeBreadcrumbs } from "../components/Resumes/ResumeBreadcrumbs";
import { Button } from "../components/ui/Button";
import { ErrorMessage } from "../components/ui/ErrorMessage";
import { LoadingBlock } from "../components/ui/LoadingBlock";
import { Input } from "../components/ui/Input";
import { Modal } from "../components/ui/Modal";
import { getApiErrorDetail } from "../services/api";
import { getVacancyMeta, listResumeStatuses, saveVacancyMeta } from "../services/resumesApi";
import type { ResumeField, ResumeStatus, VacancyMeta } from "../types/resumes";
import { generateId } from "../utils/id";
import {
  buildResumeRoute,
  isInlineResumeErrorCode,
  joinResumePath,
  normalizeWebsiteUrl,
} from "../utils/resumes";
import { formatApiError, showErrorToast, showSuccessToast } from "../utils/toast";

const HIDDEN_FILE_NAMES = ["meta.yaml"];

interface FieldDraft {
  key: string;
  name: string;
  value: string;
}

function toFieldDrafts(fields: ResumeField[]): FieldDraft[] {
  return fields.map((field) => ({ key: generateId(), name: field.name, value: field.value }));
}

function metaSignature(websiteUrl: string, statusId: string, fields: FieldDraft[]): string {
  return JSON.stringify({
    website_url: websiteUrl,
    status_id: statusId,
    fields: fields.map((field) => ({ name: field.name, value: field.value })),
  });
}

export default function ResumeVacancyPage() {
  const params = useParams();
  const country = params.country ?? "";
  const company = params.company ?? "";
  const vacancy = params.vacancy ?? "";
  const vacancyPath = joinResumePath(joinResumePath(country, company), vacancy);

  const [meta, setMeta] = useState<VacancyMeta | null>(null);
  const [statuses, setStatuses] = useState<ResumeStatus[]>([]);
  const [websiteUrl, setWebsiteUrl] = useState("");
  const [statusId, setStatusId] = useState("");
  const [fields, setFields] = useState<FieldDraft[]>([]);
  const [savedSignature, setSavedSignature] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [loadErrorCode, setLoadErrorCode] = useState<string | null>(null);
  const [statusesErrorCode, setStatusesErrorCode] = useState<string | null>(null);
  const [fieldsError, setFieldsError] = useState<string | undefined>();
  const [isSaving, setIsSaving] = useState(false);

  const isPristine = useMemo(
    () => metaSignature(websiteUrl, statusId, fields) === savedSignature,
    [fields, savedSignature, statusId, websiteUrl],
  );

  const applyMeta = useCallback((data: VacancyMeta) => {
    const fieldDrafts = toFieldDrafts(data.fields);
    setMeta(data);
    setWebsiteUrl(data.website_url);
    setStatusId(data.status_id ?? "");
    setFields(fieldDrafts);
    setSavedSignature(metaSignature(data.website_url, data.status_id ?? "", fieldDrafts));
  }, []);

  const loadVacancy = useCallback(async () => {
    setIsLoading(true);
    setLoadErrorCode(null);

    try {
      const data = await getVacancyMeta(vacancyPath);
      applyMeta(data);
    } catch (error) {
      const detail = getApiErrorDetail(error);
      setLoadErrorCode(detail?.error_code ?? "INTERNAL_ERROR");
      setMeta(null);
    } finally {
      setIsLoading(false);
    }
  }, [applyMeta, vacancyPath]);

  const loadStatuses = useCallback(async () => {
    setStatusesErrorCode(null);

    try {
      const data = await listResumeStatuses();
      setStatuses(data.statuses);
    } catch (error) {
      const detail = getApiErrorDetail(error);
      setStatusesErrorCode(detail?.error_code ?? "INTERNAL_ERROR");
      setStatuses([]);
    }
  }, []);

  useEffect(() => {
    void loadVacancy();
    void loadStatuses();
  }, [loadStatuses, loadVacancy]);

  useEffect(() => {
    if (isPristine) {
      return;
    }

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };

    window.addEventListener("beforeunload", handleBeforeUnload);

    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isPristine]);

  const blocker = useBlocker(!isPristine && meta !== null);

  useEffect(() => {
    if (blocker.state === "blocked" && isPristine) {
      blocker.reset();
    }
  }, [blocker, isPristine]);

  const handleAddField = useCallback(() => {
    setFields((current) => [...current, { key: generateId(), name: "", value: "" }]);
    setFieldsError(undefined);
  }, []);

  const handleFieldChange = useCallback(
    (key: string, patch: Partial<FieldDraft>) => {
      setFields((current) =>
        current.map((field) => (field.key === key ? { ...field, ...patch } : field)),
      );
      setFieldsError(undefined);
    },
    [],
  );

  const handleRemoveField = useCallback((key: string) => {
    setFields((current) => current.filter((field) => field.key !== key));
    setFieldsError(undefined);
  }, []);

  const handleSave = useCallback(async () => {
    const trimmedFields = fields.map((field) => ({ ...field, name: field.name.trim() }));

    if (trimmedFields.some((field) => !field.name)) {
      setFieldsError("Field names cannot be empty");
      return;
    }

    const seen = new Set<string>();

    for (const field of trimmedFields) {
      const key = field.name.toLowerCase();
      if (seen.has(key)) {
        setFieldsError(`Duplicate field name: ${field.name}`);
        return;
      }
      seen.add(key);
    }

    setIsSaving(true);
    setFieldsError(undefined);

    try {
      const data = await saveVacancyMeta(vacancyPath, {
        website_url: normalizeWebsiteUrl(websiteUrl),
        status_id: statusId || null,
        fields: trimmedFields.map((field) => ({ name: field.name, value: field.value })),
      });
      applyMeta(data);
      showSuccessToast("Vacancy saved");
    } catch (error) {
      const detail = getApiErrorDetail(error);
      if (isInlineResumeErrorCode(detail?.error_code)) {
        setFieldsError(formatApiError(detail));
      } else {
        showErrorToast(error);
      }
    } finally {
      setIsSaving(false);
    }
  }, [applyMeta, fields, statusId, vacancyPath, websiteUrl]);

  const websiteHref = normalizeWebsiteUrl(websiteUrl);

  const breadcrumbs = (
    <ResumeBreadcrumbs
      crumbs={[
        { label: "Resumes", to: "/resumes" },
        { label: country, to: buildResumeRoute([country]) },
        { label: company, to: buildResumeRoute([country, company]) },
        { label: vacancy },
      ]}
    />
  );

  if (isLoading) {
    return (
      <PageFrame title={vacancy || "Vacancy"} kicker="Resumes">
        <LoadingBlock active label="Loading vacancy…" />
      </PageFrame>
    );
  }

  if (loadErrorCode || !meta) {
    return (
      <PageFrame title={vacancy || "Vacancy"} kicker="Resumes">
        {breadcrumbs}
        <div className="error-block">
          <ErrorMessage errorCode={loadErrorCode ?? "INTERNAL_ERROR"} />
        </div>
      </PageFrame>
    );
  }

  return (
    <PageFrame
      title={vacancy || "Vacancy"}
      kicker="Resumes"
      lead="Notes, status, and files for this vacancy."
      actions={
        <Button
          type="button"
          disabled={isPristine || isSaving}
          isLoading={isSaving}
          onClick={() => {
            void handleSave();
          }}
        >
          Save
        </Button>
      }
    >
      {breadcrumbs}

      <div className="tool">
        <section className="surface stack">
          <div className="card-grid">
            <div className="field">
              <label htmlFor="vacancy-status">Status</label>
              <select
                id="vacancy-status"
                value={statusId}
                onChange={(event) => setStatusId(event.target.value)}
              >
                <option value="">No status</option>
                {statuses.map((status) => (
                  <option key={status.id} value={status.id}>
                    {status.name}
                  </option>
                ))}
                {statusId && !statuses.some((status) => status.id === statusId) && (
                  <option value={statusId}>Unknown status</option>
                )}
              </select>
              {statusesErrorCode && (
                <ErrorMessage errorCode={statusesErrorCode} />
              )}
            </div>

            <div className="stack-tight">
              <Input
                label="Website URL"
                value={websiteUrl}
                onChange={(event) => setWebsiteUrl(event.target.value)}
                placeholder="example.com/jobs/42"
                autoComplete="off"
              />
              {websiteHref && (
                <a className="text-link" href={websiteHref} target="_blank" rel="noreferrer">
                  <ExternalLink size={14} aria-hidden="true" />
                  {websiteHref}
                </a>
              )}
            </div>
          </div>

          <div className="stack">
            <div className="toolbar">
              <h3>Fields</h3>
              <Button type="button" variant="secondary" onClick={handleAddField}>
                <Plus size={16} aria-hidden="true" />
                Add field
              </Button>
            </div>

            {fields.length === 0 ? (
              <p className="empty">No fields yet.</p>
            ) : (
              <ul className="stack-tight">
                {fields.map((field) => (
                  <li key={field.key} className="field-row">
                    <input
                      type="text"
                      value={field.name}
                      onChange={(event) =>
                        handleFieldChange(field.key, { name: event.target.value })
                      }
                      placeholder="Field name"
                      aria-label="Field name"
                      autoComplete="off"
                      className="bare-input"
                    />
                    <input
                      type="text"
                      value={field.value}
                      onChange={(event) =>
                        handleFieldChange(field.key, { value: event.target.value })
                      }
                      placeholder="Value"
                      aria-label="Field value"
                      autoComplete="off"
                      className="bare-input"
                    />
                    <button
                      type="button"
                      title="Delete field"
                      aria-label={`Delete ${field.name || "field"}`}
                      className="icon-btn danger"
                      onClick={() => handleRemoveField(field.key)}
                    >
                      <Trash2 size={16} />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {fieldsError && <p className="form-error">{fieldsError}</p>}
          </div>
        </section>

        <section className="surface">
          <FileManager
            key={vacancyPath}
            apiPrefix="/api/resumes/files"
            mode="plain"
            basePath={vacancyPath}
            hiddenNames={HIDDEN_FILE_NAMES}
          />
        </section>
      </div>

      <Modal
        isOpen={blocker.state === "blocked"}
        onClose={() => blocker.reset?.()}
        title="Unsaved changes"
      >
        <p>This vacancy has unsaved changes. Leave the page and discard them?</p>
        <div className="actions">
          <Button type="button" variant="secondary" onClick={() => blocker.reset?.()}>
            Stay
          </Button>
          <Button type="button" variant="danger" onClick={() => blocker.proceed?.()}>
            Discard
          </Button>
        </div>
      </Modal>
    </PageFrame>
  );
}
