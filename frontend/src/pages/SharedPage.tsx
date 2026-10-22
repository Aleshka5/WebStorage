import { Navigate } from "react-router-dom";
import { FileManager } from "../components/FileManager/FileManager";
import { PageFrame } from "../components/Layout/PageFrame";
import { useAuthStore } from "../store/auth";

export default function SharedPage() {
  const role = useAuthStore((state) => state.user?.role);

  if (role === "STRANGER") {
    return <Navigate to="/files" replace state={{ message: "Access denied" }} />;
  }

  return (
    <PageFrame title="Shared" lead="Files the family can open together.">
      <FileManager apiPrefix="/api/shared" mode="plain" />
    </PageFrame>
  );
}
