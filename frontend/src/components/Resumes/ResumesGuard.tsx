import { Link, Outlet } from "react-router-dom";
import { useAuthStore } from "../../store/auth";
import { ErrorMessage } from "../ui/ErrorMessage";

export function ResumesGuard() {
  const role = useAuthStore((state) => state.user?.role);

  if (role !== "FAMILY" && role !== "ADMIN") {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
        <ErrorMessage errorCode="ACCESS_DENIED" />
        <Link
          to="/files"
          className="rounded-lg bg-zinc-700 px-4 py-2.5 text-sm font-medium text-zinc-100 transition-colors hover:bg-zinc-600"
        >
          Back to Files
        </Link>
      </div>
    );
  }

  return <Outlet />;
}
