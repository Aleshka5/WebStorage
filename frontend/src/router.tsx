import { useEffect, useState } from "react";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { AppLayout } from "./components/Layout/AppLayout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { Button } from "./components/ui/Button";
import { ErrorMessage } from "./components/ui/ErrorMessage";
import AdminPage from "./pages/AdminPage";
import FilesPage from "./pages/FilesPage";
import PhotosPage from "./pages/PhotosPage";
import PrivatePage from "./pages/PrivatePage";
import SharedPage from "./pages/SharedPage";
import { useAuthStore } from "./store/auth";
import { redirectToAuthLogin } from "./utils/authLogin";

function SessionBootstrap() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950">
      <span className="h-8 w-8 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />
    </div>
  );
}

function AuthUnavailableScreen({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-zinc-950 px-4">
      <ErrorMessage errorCode="AUTH_UNAVAILABLE" className="mb-4 text-center" />
      <Button type="button" onClick={onRetry} className="max-w-xs">
        Retry
      </Button>
    </div>
  );
}

function RootRedirect() {
  const user = useAuthStore((state) => state.user);

  useEffect(() => {
    if (!user) {
      redirectToAuthLogin();
    }
  }, [user]);

  if (!user) {
    return <SessionBootstrap />;
  }

  return <Navigate to="/files" replace />;
}

const router = createBrowserRouter([
  { path: "/", element: <RootRedirect /> },
  { path: "/auth", element: <RootRedirect /> },
  {
    element: (
      <ProtectedRoute>
        <AppLayout />
      </ProtectedRoute>
    ),
    children: [
      { path: "/files", element: <FilesPage /> },
      { path: "/photos", element: <PhotosPage /> },
      { path: "/private", element: <PrivatePage /> },
      { path: "/shared", element: <SharedPage /> },
      { path: "/admin", element: <AdminPage /> },
    ],
  },
]);

export function AppRouter() {
  const fetchMe = useAuthStore((state) => state.fetchMe);
  const authUnavailable = useAuthStore((state) => state.authUnavailable);
  const [sessionReady, setSessionReady] = useState(false);
  const [retryNonce, setRetryNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setSessionReady(false);

    void fetchMe().finally(() => {
      if (!cancelled) {
        setSessionReady(true);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [fetchMe, retryNonce]);

  if (!sessionReady) {
    return <SessionBootstrap />;
  }

  if (authUnavailable) {
    return (
      <AuthUnavailableScreen
        onRetry={() => {
          setRetryNonce((value) => value + 1);
        }}
      />
    );
  }

  return <RouterProvider router={router} />;
}
