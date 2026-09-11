import { useParams } from "react-router-dom";
import { ResumeBreadcrumbs } from "../components/Resumes/ResumeBreadcrumbs";
import { ResumeTreeSection } from "../components/Resumes/ResumeTreeSection";
import type { ResumeNode } from "../types/resumes";
import { buildResumeRoute } from "../utils/resumes";

export default function ResumeCountryPage() {
  const params = useParams();
  const country = params.country ?? "";

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <ResumeBreadcrumbs
        crumbs={[{ label: "Resumes", to: "/resumes" }, { label: country }]}
      />
      <h2 className="text-xl font-semibold text-zinc-100">{country}</h2>
      <div className="min-h-0 flex-1 overflow-auto">
        <ResumeTreeSection
          parentPath={country}
          entityLabel="company"
          addLabel="Add a new company"
          nameLabel="Company name"
          emptyMessage="No companies yet"
          childLabelSingular="vacancy"
          childLabelPlural="vacancies"
          buildItemHref={(node: ResumeNode) => buildResumeRoute([country, node.name])}
        />
      </div>
    </div>
  );
}
