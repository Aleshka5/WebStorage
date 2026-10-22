import { useEffect, useState } from "react";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { AppLayout, Shell } from "./components/Layout/AppLayout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { ResumesGuard } from "./components/Resumes/ResumesGuard";
import { Button } from "./components/ui/Button";
import { LoadingBlock } from "./components/ui/LoadingBlock";
import { ErrorMessage } from "./components/ui/ErrorMessage";
import AdminPage from "./pages/AdminPage";
import AllVacanciesPage from "./pages/AllVacanciesPage";
import FilesPage from "./pages/FilesPage";
import GeneratedImagePage from "./pages/GeneratedImagePage";
import GeneratedImagesPage from "./pages/GeneratedImagesPage";
import PhotosPage from "./pages/PhotosPage";
import KeysRegistryPage from "./pages/KeysRegistryPage";
import PrivatePage from "./pages/PrivatePage";
import ResumeCompanyPage from "./pages/ResumeCompanyPage";
import ResumeCountryPage from "./pages/ResumeCountryPage";
import ResumeVacancyPage from "./pages/ResumeVacancyPage";
import ResumesPage from "./pages/ResumesPage";
import SharedPage from "./pages/SharedPage";
import { useAuthStore } from "./store/auth";

function SessionBootstrap() {
  return <LoadingBlock active label="Checking sign-in…" />;
}

function UserServiceUnavailableScreen({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="error-block">
      <ErrorMessage errorCode="USER_SERVICE_UNAVAILABLE" />
      <Button type="button" onClick={onRetry}>
        Reload
      </Button>
    </div>
  );
}

function UnauthorizedScreen() {
  return (
    <div className="error-block">
      <ErrorMessage
        errorCode="UNAUTHORIZED"
        message="Open this site through the hub. Storage does not handle sign-in."
      />
    </div>
  );
}

function RootRedirect() {
  const user = useAuthStore((state) => state.user);

  if (!user) {
    return <UnauthorizedScreen />;
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
      { path: "/generated", element: <GeneratedImagesPage /> },
      { path: "/generated/:id", element: <GeneratedImagePage /> },
      { path: "/private", element: <PrivatePage /> },
      { path: "/keys", element: <KeysRegistryPage /> },
      {
        path: "/resumes",
        element: <ResumesGuard />,
        children: [
          { index: true, element: <ResumesPage /> },
          { path: ":country", element: <ResumeCountryPage /> },
          { path: ":country/:company", element: <ResumeCompanyPage /> },
          { path: ":country/:company/:vacancy", element: <ResumeVacancyPage /> },
        ],
      },
      {
        path: "/vacancies",
        element: <ResumesGuard />,
        children: [{ index: true, element: <AllVacanciesPage /> }],
      },
      { path: "/shared", element: <SharedPage /> },
      { path: "/admin", element: <AdminPage /> },
    ],
  },
]);

export function AppRouter() {
  const fetchMe = useAuthStore((state) => state.fetchMe);
  const userServiceUnavailable = useAuthStore((state) => state.userServiceUnavailable);
  const unauthorized = useAuthStore((state) => state.unauthorized);
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
    return (
      <Shell>
        <SessionBootstrap />
      </Shell>
    );
  }

  if (userServiceUnavailable) {
    return (
      <Shell>
        <UserServiceUnavailableScreen
          onRetry={() => {
            setRetryNonce((value) => value + 1);
          }}
        />
      </Shell>
    );
  }

  if (unauthorized) {
    return (
      <Shell>
        <UnauthorizedScreen />
      </Shell>
    );
  }

  return <RouterProvider router={router} />;
}
