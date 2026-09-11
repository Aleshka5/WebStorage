import type { ResumeStatus } from "../../types/resumes";

interface ResumeStatusBadgeProps {
  status?: ResumeStatus;
  className?: string;
}

export function ResumeStatusBadge({ status, className = "" }: ResumeStatusBadgeProps) {
  return (
    <span className={`inline-flex items-center gap-2 text-xs ${className}`.trim()}>
      <span
        className="h-2.5 w-2.5 shrink-0 rounded-full"
        style={{ backgroundColor: status ? status.color : "#52525B" }}
        aria-hidden="true"
      />
      <span className={status ? "text-zinc-300" : "text-zinc-500"}>
        {status ? status.name : "No status"}
      </span>
    </span>
  );
}
