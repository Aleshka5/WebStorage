import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { SlidersHorizontal } from "lucide-react";
import { PageFrame } from "../components/Layout/PageFrame";
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
    <div className="tool">
      <div className="actions">
        <Link className="btn btn-ghost" to="/vacancies">
          All vacancies
        </Link>
        <Button
          type="button"
          variant="secondary"
          onClick={() => setIsEditorOpen((current) => !current)}
        >
          <SlidersHorizontal size={16} aria-hidden="true" />
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
    <PageFrame title={company || "Company"} kicker="Resumes" lead="Vacancies at this company.">
      <ResumeBreadcrumbs
        crumbs={[
          { label: "Resumes", to: "/resumes" },
          { label: country, to: buildResumeRoute([country]) },
          { label: company },
        ]}
      />
      <div>
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
    </PageFrame>
  );
}
