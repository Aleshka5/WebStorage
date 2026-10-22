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
    <nav aria-label="Resumes navigation" className="crumbs">
      {crumbs.map((crumb, index) => (
        <span key={`${crumb.label}-${index}`} className="cluster">
          {index > 0 && <span aria-hidden="true">/</span>}
          {crumb.to ? (
            <Link to={crumb.to}>{crumb.label}</Link>
          ) : (
            <span className="is-current">{crumb.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}
