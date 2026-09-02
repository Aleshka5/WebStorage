import { useAuthStore } from "../store/auth";
import { ErrorMessage } from "./ui/ErrorMessage";

interface ProtectedRouteProps {
  children: React.ReactNode;
}

export function ProtectedRoute({ children }: ProtectedRouteProps) {
  const user = useAuthStore((state) => state.user);

  if (!user) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-zinc-950 px-4">
        <ErrorMessage
          errorCode="UNAUTHORIZED"
          message="Open this site through the hub. Storage does not handle sign-in."
          className="text-center"
        />
      </div>
    );
  }

  return children;
}
