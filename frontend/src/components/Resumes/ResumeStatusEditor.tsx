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
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/70 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-zinc-100">Statuses</h3>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close status editor"
          className="rounded p-1 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-300"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {drafts.length === 0 ? (
        <p className="mb-3 text-sm text-zinc-400">No statuses yet</p>
      ) : (
        <ul className="mb-3 flex flex-col gap-2">
          {drafts.map((draft) => (
            <li key={draft.key} className="flex items-center gap-2">
              <input
                type="color"
                value={HEX_COLOR_PATTERN.test(draft.color) ? draft.color : DEFAULT_STATUS_COLOR}
                onChange={(event) =>
                  updateDraft(draft.key, { color: event.target.value.toUpperCase() })
                }
                aria-label={`Colour for ${draft.name || "new status"}`}
                className="h-9 w-10 shrink-0 cursor-pointer rounded-lg border border-zinc-700 bg-zinc-800/80 p-1"
              />
              <input
                type="text"
                value={draft.name}
                onChange={(event) => updateDraft(draft.key, { name: event.target.value })}
                placeholder="Status name"
                aria-label="Status name"
                autoComplete="off"
                className="w-full rounded-lg border border-zinc-700 bg-zinc-800/80 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 transition-colors hover:border-zinc-600 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
              />
              <span className="hidden w-20 shrink-0 font-mono text-xs text-zinc-500 sm:inline">
                {draft.color}
              </span>
              <button
                type="button"
                title="Delete status"
                aria-label={`Delete ${draft.name || "status"}`}
                className="rounded-lg p-2 text-zinc-400 transition-colors hover:bg-red-500/10 hover:text-red-400"
                onClick={() => handleRemove(draft.key)}
              >
                <Trash2 size={16} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && <p className="mb-3 text-xs text-red-400">{error}</p>}

      <p className="mb-3 text-xs text-zinc-500">
        Deleting a status only removes it from this list. Vacancies that used it show “No status”.
      </p>

      <div className="flex gap-2">
        <Button type="button" variant="secondary" className="w-auto" onClick={handleAdd}>
          <Plus size={16} className="mr-1.5" />
          Add status
        </Button>
        <Button
          type="button"
          className="w-auto"
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
