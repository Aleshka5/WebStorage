import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Briefcase } from "lucide-react";
import { ResumeStatusBadge } from "../components/Resumes/ResumeStatusBadge";
import {
  NO_STATUS_KEY,
  VacancyStatusFilter,
  type StatusFilterMode,
} from "../components/Resumes/VacancyStatusFilter";
import { ErrorMessage } from "../components/ui/ErrorMessage";
import { getApiErrorDetail } from "../services/api";
import { listAllVacancies, listResumeStatuses } from "../services/resumesApi";
import type { ResumeStatus, VacancyListItem } from "../types/resumes";
import { buildResumeRoute } from "../utils/resumes";
import { showErrorToast } from "../utils/toast";

export default function AllVacanciesPage() {
  const [items, setItems] = useState<VacancyListItem[]>([]);
  const [statuses, setStatuses] = useState<ResumeStatus[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadErrorCode, setLoadErrorCode] = useState<string | null>(null);
  const [filterMode, setFilterMode] = useState<StatusFilterMode>("include");
  const [selectedStatusKeys, setSelectedStatusKeys] = useState<string[]>([]);

  const statusById = useMemo(
    () => new Map(statuses.map((status) => [status.id, status])),
    [statuses],
  );

  const visibleItems = useMemo(() => {
    if (selectedStatusKeys.length === 0) {
      return items;
    }

    const selected = new Set(selectedStatusKeys);

    return items.filter((item) => {
      const key =
        item.status_id && statusById.has(item.status_id) ? item.status_id : NO_STATUS_KEY;
      return filterMode === "include" ? selected.has(key) : !selected.has(key);
    });
  }, [filterMode, items, selectedStatusKeys, statusById]);

  const load = useCallback(async () => {
    setIsLoading(true);
    setLoadErrorCode(null);

    try {
      const [vacancies, statusList] = await Promise.all([
        listAllVacancies(),
        listResumeStatuses(),
      ]);
      setItems(vacancies.items);
      setStatuses(statusList.statuses);
    } catch (error) {
      const detail = getApiErrorDetail(error);
      setLoadErrorCode(detail?.error_code ?? "INTERNAL_ERROR");
      showErrorToast(error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-semibold text-zinc-100">All vacancies</h2>
        <Link
          to="/resumes"
          className="rounded-lg bg-zinc-800 px-3 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-zinc-700 hover:text-zinc-100"
        >
          Back to countries
        </Link>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {isLoading ? (
          <div className="flex justify-center py-12">
            <span className="h-8 w-8 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />
          </div>
        ) : loadErrorCode ? (
          <ErrorMessage errorCode={loadErrorCode} />
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-zinc-700 px-6 py-16 text-center">
            <Briefcase className="h-8 w-8 text-zinc-600" aria-hidden="true" />
            <p className="text-sm text-zinc-400">No vacancies yet</p>
            <Link
              to="/resumes"
              className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-500"
            >
              Go to Resumes
            </Link>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <VacancyStatusFilter
              statuses={statuses}
              mode={filterMode}
              selected={selectedStatusKeys}
              onModeChange={setFilterMode}
              onSelectedChange={setSelectedStatusKeys}
              matchedCount={visibleItems.length}
              totalCount={items.length}
            />

            {visibleItems.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-zinc-700 px-6 py-16 text-center">
                <p className="text-sm text-zinc-400">No vacancies match this filter</p>
                <button
                  type="button"
                  onClick={() => setSelectedStatusKeys([])}
                  className="rounded-lg bg-zinc-800 px-4 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-zinc-700 hover:text-zinc-100"
                >
                  Clear filter
                </button>
              </div>
            ) : (
              <div className="overflow-hidden rounded-lg border border-zinc-800">
                <table className="w-full text-left text-sm">
                  <thead className="bg-zinc-900/80 text-xs uppercase tracking-wide text-zinc-500">
                    <tr>
                      <th className="px-4 py-3 font-medium">Country</th>
                      <th className="px-4 py-3 font-medium">Company</th>
                      <th className="px-4 py-3 font-medium">Vacancy</th>
                      <th className="px-4 py-3 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800">
                    {visibleItems.map((item) => (
                      <tr key={item.path} className="bg-zinc-950/40">
                        <td className="px-4 py-3">
                          <Link
                            to={buildResumeRoute([item.country])}
                            className="rounded text-zinc-300 transition-colors hover:text-sky-400"
                          >
                            {item.country}
                          </Link>
                        </td>
                        <td className="px-4 py-3">
                          <Link
                            to={buildResumeRoute([item.country, item.company])}
                            className="rounded text-zinc-300 transition-colors hover:text-sky-400"
                          >
                            {item.company}
                          </Link>
                        </td>
                        <td className="px-4 py-3">
                          <Link
                            to={buildResumeRoute([item.country, item.company, item.name])}
                            className="rounded font-medium text-zinc-100 transition-colors hover:text-sky-400"
                          >
                            {item.name}
                          </Link>
                        </td>
                        <td className="px-4 py-3">
                          <ResumeStatusBadge
                            status={item.status_id ? statusById.get(item.status_id) : undefined}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
