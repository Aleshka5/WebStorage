import { Link, Outlet } from "react-router-dom";
import { useAuthStore } from "../../store/auth";
import { ErrorMessage } from "../ui/ErrorMessage";

export function ResumesGuard() {
  const role = useAuthStore((state) => state.user?.role);

  if (role !== "FAMILY" && role !== "ADMIN") {
    return (
      <div className="error-block">
        <ErrorMessage errorCode="ACCESS_DENIED" />
        <Link className="btn" to="/files">
          Back to Files
        </Link>
      </div>
    );
  }

  return <Outlet />;
}
