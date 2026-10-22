import { useParams } from "react-router-dom";
import { PageFrame } from "../components/Layout/PageFrame";
import { ResumeBreadcrumbs } from "../components/Resumes/ResumeBreadcrumbs";
import { ResumeTreeSection } from "../components/Resumes/ResumeTreeSection";
import type { ResumeNode } from "../types/resumes";
import { buildResumeRoute } from "../utils/resumes";

export default function ResumeCountryPage() {
  const params = useParams();
  const country = params.country ?? "";

  return (
    <PageFrame title={country || "Country"} kicker="Resumes" lead="Companies in this country.">
      <ResumeBreadcrumbs crumbs={[{ label: "Resumes", to: "/resumes" }, { label: country }]} />
      <ResumeTreeSection
        parentPath={country}
        entityLabel="company"
        addLabel="Add a new company"
        nameLabel="Company name"
        emptyMessage="No companies yet."
        childLabelSingular="vacancy"
        childLabelPlural="vacancies"
        buildItemHref={(node: ResumeNode) => buildResumeRoute([country, node.name])}
      />
    </PageFrame>
  );
}
