import { useCallback, useEffect, useMemo, useState } from "react";
import { Plus, Trash2, X } from "lucide-react";
import { getApiErrorDetail } from "../../services/api";
import { saveResumeStatuses } from "../../services/resumesApi";
import type { ResumeStatus, ResumeStatusInput } from "../../types/resumes";
import { generateId } from "../../utils/id";
import { DEFAULT_STATUS_COLOR, HEX_COLOR_PATTERN } from "../../utils/resumes";
import { formatApiError, showErrorToast, showSuccessToast } from "../../utils/toast";
import { Button } from "../ui/Button";

interface StatusDraft {
  key: string;
  id?: string;
  name: string;
  color: string;
}

interface ResumeStatusEditorProps {
  statuses: ResumeStatus[];
  onClose: () => void;
  onSaved: (statuses: ResumeStatus[]) => void;
}

function toDrafts(statuses: ResumeStatus[]): StatusDraft[] {
  return statuses.map((status) => ({
    key: status.id,
    id: status.id,
    name: status.name,
    color: status.color,
  }));
}

function draftsSignature(drafts: StatusDraft[]): string {
  return JSON.stringify(
    drafts.map((draft) => ({ id: draft.id ?? null, name: draft.name, color: draft.color })),
  );
}

export function ResumeStatusEditor({ statuses, onClose, onSaved }: ResumeStatusEditorProps) {
  const [drafts, setDrafts] = useState<StatusDraft[]>(() => toDrafts(statuses));
  const [savedSignature, setSavedSignature] = useState(() => draftsSignature(toDrafts(statuses)));
  const [error, setError] = useState<string | undefined>();
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    const initialDrafts = toDrafts(statuses);
    setDrafts(initialDrafts);
    setSavedSignature(draftsSignature(initialDrafts));
  }, [statuses]);

  const isPristine = useMemo(
    () => draftsSignature(drafts) === savedSignature,
    [drafts, savedSignature],
  );

  const updateDraft = useCallback((key: string, patch: Partial<StatusDraft>) => {
    setDrafts((current) =>
      current.map((draft) => (draft.key === key ? { ...draft, ...patch } : draft)),
    );
    setError(undefined);
  }, []);

  const handleAdd = useCallback(() => {
    setDrafts((current) => [
      ...current,
      { key: generateId(), name: "", color: DEFAULT_STATUS_COLOR },
    ]);
    setError(undefined);
  }, []);

  const handleRemove = useCallback((key: string) => {
    setDrafts((current) => current.filter((draft) => draft.key !== key));
    setError(undefined);
  }, []);

  const handleSave = useCallback(async () => {
    const trimmed = drafts.map((draft) => ({ ...draft, name: draft.name.trim() }));

    if (trimmed.some((draft) => !draft.name)) {
      setError("Status names cannot be empty");
      return;
    }

    const seen = new Set<string>();

    for (const draft of trimmed) {
      const key = draft.name.toLowerCase();
      if (seen.has(key)) {
        setError(`Duplicate status name: ${draft.name}`);
        return;
      }
      seen.add(key);
    }

    if (trimmed.some((draft) => !HEX_COLOR_PATTERN.test(draft.color))) {
      setError("Colours must be in #RRGGBB format");
      return;
    }

    setIsSaving(true);
    setError(undefined);

    try {
      const payload: ResumeStatusInput[] = trimmed.map((draft) =>
        draft.id
          ? { id: draft.id, name: draft.name, color: draft.color }
          : { name: draft.name, color: draft.color },
      );
      const data = await saveResumeStatuses(payload);
      const savedDrafts = toDrafts(data.statuses);
      setDrafts(savedDrafts);
      setSavedSignature(draftsSignature(savedDrafts));
      onSaved(data.statuses);
      showSuccessToast("Statuses saved");
    } catch (saveError) {
      const detail = getApiErrorDetail(saveError);
      setError(formatApiError(detail));
      showErrorToast(saveError);
    } finally {
      setIsSaving(false);
    }
  }, [drafts, onSaved]);

  return (
    <section className="surface stack">
      <div className="toolbar">
        <h3>Statuses</h3>
        <button type="button" onClick={onClose} aria-label="Close status editor" className="icon-btn">
          <X size={16} />
        </button>
      </div>

      {drafts.length === 0 ? (
        <p className="empty">No statuses yet.</p>
      ) : (
        <ul className="stack-tight">
          {drafts.map((draft) => (
            <li key={draft.key} className="field-row">
              <input
                type="color"
                value={HEX_COLOR_PATTERN.test(draft.color) ? draft.color : DEFAULT_STATUS_COLOR}
                onChange={(event) =>
                  updateDraft(draft.key, { color: event.target.value.toUpperCase() })
                }
                aria-label={`Colour for ${draft.name || "new status"}`}
                className="color-input"
              />
              <input
                type="text"
                value={draft.name}
                onChange={(event) => updateDraft(draft.key, { name: event.target.value })}
                placeholder="Status name"
                aria-label="Status name"
                autoComplete="off"
                className="bare-input"
              />
              <span className="meta-code hide-sm">{draft.color}</span>
              <button
                type="button"
                title="Delete status"
                aria-label={`Delete ${draft.name || "status"}`}
                className="icon-btn danger"
                onClick={() => handleRemove(draft.key)}
              >
                <Trash2 size={16} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && <p className="form-error">{error}</p>}

      <p className="muted">
        Deleting a status only removes it from this list. Vacancies that used it show “No status”.
      </p>

      <div className="actions">
        <Button type="button" variant="secondary" onClick={handleAdd}>
          <Plus size={16} aria-hidden="true" />
          Add status
        </Button>
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
      </div>
    </section>
  );
}
