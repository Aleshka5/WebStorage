import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { SlidersHorizontal } from "lucide-react";
import { ResumeBreadcrumbs } from "../components/Resumes/ResumeBreadcrumbs";
import { ResumeStatusBadge } from "../components/Resumes/ResumeStatusBadge";
import { ResumeStatusEditor } from "../components/Resumes/ResumeStatusEditor";
import { ResumeTreeSection } from "../components/Resumes/ResumeTreeSection";
import { Button } from "../components/ui/Button";
import { ErrorMessage } from "../components/ui/ErrorMessage";
import { getApiErrorDetail } from "../services/api";
import { listResumeStatuses } from "../services/resumesApi";
import type { ResumeNode, ResumeStatus } from "../types/resumes";
import { buildResumeRoute, joinResumePath } from "../utils/resumes";

export default function ResumeCompanyPage() {
  const params = useParams();
  const country = params.country ?? "";
  const company = params.company ?? "";
  const parentPath = joinResumePath(country, company);

  const [statuses, setStatuses] = useState<ResumeStatus[]>([]);
  const [statusesErrorCode, setStatusesErrorCode] = useState<string | null>(null);
  const [isEditorOpen, setIsEditorOpen] = useState(false);

  const statusById = useMemo(
    () => new Map(statuses.map((status) => [status.id, status])),
    [statuses],
  );

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
    void loadStatuses();
  }, [loadStatuses]);

  const handleStatusesSaved = useCallback((saved: ResumeStatus[]) => {
    setStatuses(saved);
  }, []);

  const renderItemMeta = useCallback(
    (node: ResumeNode) => (
      <ResumeStatusBadge
        status={node.status_id ? statusById.get(node.status_id) : undefined}
        className="shrink-0"
      />
    ),
    [statusById],
  );

  const toolbar = (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link
          to="/vacancies"
          className="rounded-lg bg-zinc-800 px-3 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-zinc-700 hover:text-zinc-100"
        >
          All vacancies
        </Link>
        <Button
          type="button"
          variant="secondary"
          className="w-auto"
          onClick={() => setIsEditorOpen((current) => !current)}
        >
          <SlidersHorizontal size={16} className="mr-1.5" />
          Edit statuses
        </Button>
      </div>

      {statusesErrorCode && <ErrorMessage errorCode={statusesErrorCode} />}

      {isEditorOpen && (
        <ResumeStatusEditor
          statuses={statuses}
          onClose={() => setIsEditorOpen(false)}
          onSaved={handleStatusesSaved}
        />
      )}
    </div>
  );

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <ResumeBreadcrumbs
        crumbs={[
          { label: "Resumes", to: "/resumes" },
          { label: country, to: buildResumeRoute([country]) },
          { label: company },
        ]}
      />
      <h2 className="text-xl font-semibold text-zinc-100">{company}</h2>
      <div className="min-h-0 flex-1 overflow-auto">
        <ResumeTreeSection
          parentPath={parentPath}
          entityLabel="vacancy"
          addLabel="Add a new vacancy"
          nameLabel="Vacancy name"
          emptyMessage="No vacancies yet"
          childLabelSingular="file"
          childLabelPlural="files"
          statuses={statuses}
          toolbar={toolbar}
          renderItemMeta={renderItemMeta}
          buildItemHref={(node: ResumeNode) => buildResumeRoute([country, company, node.name])}
        />
      </div>
    </div>
  );
}
