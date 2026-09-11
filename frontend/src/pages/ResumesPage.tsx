import { Link } from "react-router-dom";
import { ResumeTreeSection } from "../components/Resumes/ResumeTreeSection";
import type { ResumeNode } from "../types/resumes";
import { buildResumeRoute } from "../utils/resumes";

export default function ResumesPage() {
  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-semibold text-zinc-100">Resumes</h2>
        <Link
          to="/vacancies"
          className="rounded-lg bg-zinc-800 px-3 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-zinc-700 hover:text-zinc-100"
        >
          All vacancies
        </Link>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <ResumeTreeSection
          parentPath=""
          entityLabel="country"
          addLabel="Add new country"
          nameLabel="Country name"
          emptyMessage="No countries yet"
          childLabelSingular="company"
          childLabelPlural="companies"
          buildItemHref={(node: ResumeNode) => buildResumeRoute([node.name])}
        />
      </div>
    </div>
  );
}
