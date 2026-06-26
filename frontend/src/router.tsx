import { useEffect, useState } from "react";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { ProtectedRoute } from "./components/ProtectedRoute";
import AuthPage from "./pages/AuthPage";
import { useAuthStore } from "./store/auth";

function RootRedirect() {
  const user = useAuthStore((state) => state.user);
  return <Navigate to={user ? "/files" : "/auth"} replace />;
}

function AuthRoute() {
  const user = useAuthStore((state) => state.user);

  if (user) {
    return <Navigate to="/files" replace />;
  }

  return <AuthPage />;
}

function FilesPlaceholder() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 text-zinc-400">
      <p className="text-sm">/files — страница будет добавлена позже</p>
    </div>
  );
}

const router = createBrowserRouter([
  { path: "/", element: <RootRedirect /> },
  { path: "/auth", element: <AuthRoute /> },
  {
    path: "/files",
    element: (
      <ProtectedRoute>
        <FilesPlaceholder />
      </ProtectedRoute>
    ),
  },
]);

function SessionBootstrap() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950">
      <span className="h-8 w-8 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />
    </div>
  );
}

export function AppRouter() {
  const fetchMe = useAuthStore((state) => state.fetchMe);
  const [sessionReady, setSessionReady] = useState(false);

  useEffect(() => {
    void fetchMe().finally(() => setSessionReady(true));
  }, [fetchMe]);

  if (!sessionReady) {
    return <SessionBootstrap />;
  }

  return <RouterProvider router={router} />;
}
