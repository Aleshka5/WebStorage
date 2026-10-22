import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { PageFrame } from "../components/Layout/PageFrame";
import { ResumeStatusBadge } from "../components/Resumes/ResumeStatusBadge";
import {
  NO_STATUS_KEY,
  VacancyStatusFilter,
  type StatusFilterMode,
} from "../components/Resumes/VacancyStatusFilter";
import { ErrorMessage } from "../components/ui/ErrorMessage";
import { LoadingBlock } from "../components/ui/LoadingBlock";
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
    <PageFrame
      title="Vacancies"
      kicker="Resumes"
      lead="Every vacancy, across countries and companies."
      actions={
        <Link className="btn btn-ghost" to="/resumes">
          Countries
        </Link>
      }
    >
      {isLoading ? (
        <LoadingBlock active label="Loading vacancies…" />
      ) : loadErrorCode ? (
        <div className="error-block">
          <ErrorMessage errorCode={loadErrorCode} />
          <button type="button" className="btn" onClick={() => void load()}>
            Reload
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="empty">
          <p>No vacancies yet.</p>
          <Link className="btn" to="/resumes">
            Go to Resumes
          </Link>
        </div>
      ) : (
        <div className="tool">
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
            <div className="empty">
              <p>No vacancies match this filter.</p>
              <button type="button" className="btn btn-ghost" onClick={() => setSelectedStatusKeys([])}>
                Clear filter
              </button>
            </div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Country</th>
                    <th>Company</th>
                    <th>Vacancy</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleItems.map((item) => (
                    <tr key={item.path}>
                      <td>
                        <Link className="text-link" to={buildResumeRoute([item.country])}>
                          {item.country}
                        </Link>
                      </td>
                      <td>
                        <Link className="text-link" to={buildResumeRoute([item.country, item.company])}>
                          {item.company}
                        </Link>
                      </td>
                      <td>
                        <Link
                          className="text-link"
                          to={buildResumeRoute([item.country, item.company, item.name])}
                        >
                          {item.name}
                        </Link>
                      </td>
                      <td>
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
    </PageFrame>
  );
}
