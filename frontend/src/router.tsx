import { useEffect, useState } from "react";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { AppLayout } from "./components/Layout/AppLayout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import AdminPage from "./pages/AdminPage";
import AuthPage from "./pages/AuthPage";
import FilesPage from "./pages/FilesPage";
import PhotosPage from "./pages/PhotosPage";
import PrivatePage from "./pages/PrivatePage";
import SharedPage from "./pages/SharedPage";
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

const router = createBrowserRouter([
  { path: "/", element: <RootRedirect /> },
  { path: "/auth", element: <AuthRoute /> },
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
