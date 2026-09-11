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

  const options: Array<{ key: string; name: string; color: string }> = [
    ...statuses.map((status) => ({ key: status.id, name: status.name, color: status.color })),
    { key: NO_STATUS_KEY, name: "No status", color: "#52525B" },
  ];

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
      <div className="flex flex-wrap items-center gap-3">
        <div
          role="radiogroup"
          aria-label="Status filter mode"
          className="inline-flex overflow-hidden rounded-lg border border-zinc-700"
        >
          {MODES.map((option) => (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={mode === option.value}
              title={option.hint}
              onClick={() => onModeChange(option.value)}
              className={[
                "px-3 py-1.5 text-sm font-medium transition-colors",
                mode === option.value
                  ? "bg-sky-600/20 text-sky-300"
                  : "bg-zinc-800/60 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200",
              ].join(" ")}
            >
              {option.label}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {options.map((option) => {
            const checked = selectedSet.has(option.key);
            return (
              <label
                key={option.key}
                className={[
                  "inline-flex cursor-pointer items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition-colors",
                  checked
                    ? "border-sky-500/60 bg-sky-500/10 text-zinc-100"
                    : "border-zinc-700 bg-zinc-800/60 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200",
                ].join(" ")}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggle(option.key)}
                  className="h-3.5 w-3.5 accent-sky-500"
                />
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: option.color }}
                  aria-hidden="true"
                />
                {option.name}
              </label>
            );
          })}
        </div>

        {isActive && (
          <button
            type="button"
            onClick={() => onSelectedChange([])}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100"
          >
            <X size={14} />
            Clear
          </button>
        )}
      </div>

      <p className="text-xs text-zinc-500">
        {isActive
          ? `Showing ${matchedCount} of ${totalCount} vacancies`
          : `${totalCount} ${totalCount === 1 ? "vacancy" : "vacancies"} — select statuses to filter`}
      </p>
    </div>
  );
}
