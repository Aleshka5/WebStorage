import { Navigate } from "react-router-dom";
import { FileManager } from "../components/FileManager/FileManager";
import { useAuthStore } from "../store/auth";

export default function SharedPage() {
  const role = useAuthStore((state) => state.user?.role);

  if (role === "STRANGER") {
    return <Navigate to="/files" replace state={{ message: "Нет доступа" }} />;
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <h2 className="text-xl font-semibold text-zinc-100">Общее</h2>
      <div className="min-h-0 flex-1">
        <FileManager apiPrefix="/api/shared" mode="plain" />
      </div>
    </div>
  );
}
