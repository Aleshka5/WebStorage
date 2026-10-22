import { Link } from "react-router-dom";
import { PageFrame } from "../components/Layout/PageFrame";
import { ResumeTreeSection } from "../components/Resumes/ResumeTreeSection";
import type { ResumeNode } from "../types/resumes";
import { buildResumeRoute } from "../utils/resumes";

export default function ResumesPage() {
  return (
    <PageFrame
      title="Resumes"
      lead="Countries, companies, and vacancies kept as folders."
      actions={
        <Link className="btn btn-ghost" to="/vacancies">
          All vacancies
        </Link>
      }
    >
      <ResumeTreeSection
        parentPath=""
        entityLabel="country"
        addLabel="Add new country"
        nameLabel="Country name"
        emptyMessage="No countries yet."
        childLabelSingular="company"
        childLabelPlural="companies"
        buildItemHref={(node: ResumeNode) => buildResumeRoute([node.name])}
      />
    </PageFrame>
  );
}
