import { useEffect } from "react";
import { useAuthStore } from "../store/auth";
import { redirectToAuthLogin } from "../utils/authLogin";

interface ProtectedRouteProps {
  children: React.ReactNode;
}

function RedirectSpinner() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950">
      <span className="h-8 w-8 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />
    </div>
  );
}

export function ProtectedRoute({ children }: ProtectedRouteProps) {
  const user = useAuthStore((state) => state.user);

  useEffect(() => {
    if (!user) {
      redirectToAuthLogin();
    }
  }, [user]);

  if (!user) {
    return <RedirectSpinner />;
  }

  return children;
}
