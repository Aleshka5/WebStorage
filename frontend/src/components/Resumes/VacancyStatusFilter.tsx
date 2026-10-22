import { X } from "lucide-react";
import type { ResumeStatus } from "../../types/resumes";

export const NO_STATUS_KEY = "NONE";

export type StatusFilterMode = "include" | "exclude";

interface VacancyStatusFilterProps {
  statuses: ResumeStatus[];
  mode: StatusFilterMode;
  selected: string[];
  onModeChange: (mode: StatusFilterMode) => void;
  onSelectedChange: (selected: string[]) => void;
  matchedCount: number;
  totalCount: number;
}

const MODES: Array<{ value: StatusFilterMode; label: string; hint: string }> = [
  { value: "include", label: "Show only", hint: "Show only the selected statuses" },
  { value: "exclude", label: "Hide", hint: "Hide the selected statuses" },
];

export function VacancyStatusFilter({
  statuses,
  mode,
  selected,
  onModeChange,
  onSelectedChange,
  matchedCount,
  totalCount,
}: VacancyStatusFilterProps) {
  const selectedSet = new Set(selected);
  const isActive = selected.length > 0;

  const toggle = (key: string) => {
    onSelectedChange(
      selectedSet.has(key) ? selected.filter((item) => item !== key) : [...selected, key],
    );
  };

  const options: Array<{ key: string; name: string; color?: string }> = [
    ...statuses.map((status) => ({ key: status.id, name: status.name, color: status.color })),
    { key: NO_STATUS_KEY, name: "No status" },
  ];

  return (
    <div className="surface stack-tight">
      <div className="cluster">
        <div className="segment" role="radiogroup" aria-label="Status filter mode">
          {MODES.map((option) => (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={mode === option.value}
              title={option.hint}
              onClick={() => onModeChange(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>

        <div className="cluster">
          {options.map((option) => {
            const checked = selectedSet.has(option.key);
            return (
              <label key={option.key} className={checked ? "choice-chip is-on" : "choice-chip"}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggle(option.key)}
                  className="sr-only"
                />
                <span
                  className={option.color ? "swatch" : "swatch muted"}
                  style={option.color ? { backgroundColor: option.color } : undefined}
                  aria-hidden="true"
                />
                {option.name}
              </label>
            );
          })}
        </div>

        {isActive && (
          <button type="button" className="btn btn-ghost" onClick={() => onSelectedChange([])}>
            <X size={14} aria-hidden="true" />
            Clear
          </button>
        )}
      </div>

      <p className="muted">
        {isActive
          ? `Showing ${matchedCount} of ${totalCount} vacancies`
          : `${totalCount} ${totalCount === 1 ? "vacancy" : "vacancies"}. Select statuses to filter.`}
      </p>
    </div>
  );
}
