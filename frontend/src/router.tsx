import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import AuthPage from "./pages/AuthPage";

const AUTH_TOKEN_KEY = "homecloud_auth";

export function hasAuthToken(): boolean {
  return localStorage.getItem(AUTH_TOKEN_KEY) === "1";
}

function RootRedirect() {
  return <Navigate to={hasAuthToken() ? "/files" : "/auth"} replace />;
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
  { path: "/auth", element: <AuthPage /> },
  { path: "/files", element: <FilesPlaceholder /> },
]);

export function AppRouter() {
  return <RouterProvider router={router} />;
}
