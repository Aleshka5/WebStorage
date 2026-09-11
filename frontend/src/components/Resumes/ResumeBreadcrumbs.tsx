import { ChevronRight } from "lucide-react";
import { Link } from "react-router-dom";

export interface ResumeCrumb {
  label: string;
  to?: string;
}

interface ResumeBreadcrumbsProps {
  crumbs: ResumeCrumb[];
}

export function ResumeBreadcrumbs({ crumbs }: ResumeBreadcrumbsProps) {
  return (
    <nav aria-label="Resumes navigation" className="flex flex-wrap items-center gap-1 text-sm">
      {crumbs.map((crumb, index) => (
        <span key={`${crumb.label}-${index}`} className="inline-flex items-center gap-1">
          {index > 0 && <ChevronRight className="h-4 w-4 text-zinc-600" aria-hidden="true" />}
          {crumb.to ? (
            <Link
              to={crumb.to}
              className="rounded px-1.5 py-0.5 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100"
            >
              {crumb.label}
            </Link>
          ) : (
            <span className="rounded px-1.5 py-0.5 text-zinc-100">{crumb.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}
