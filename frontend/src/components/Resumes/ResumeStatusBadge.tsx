import type { ResumeStatus } from "../../types/resumes";

interface ResumeStatusBadgeProps {
  status?: ResumeStatus;
  className?: string;
}

export function ResumeStatusBadge({ status, className = "" }: ResumeStatusBadgeProps) {
  return (
    <span className={["tag", className].filter(Boolean).join(" ")}>
      <span
        className={status ? "swatch" : "swatch muted"}
        style={status ? { backgroundColor: status.color } : undefined}
        aria-hidden="true"
      />
      {status ? status.name : "No status"}
    </span>
  );
}
