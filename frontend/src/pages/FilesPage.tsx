import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { FileManager } from "../components/FileManager/FileManager";

export default function FilesPage() {
  const location = useLocation();
  const [flashMessage, setFlashMessage] = useState<string | null>(null);

  useEffect(() => {
    const message = (location.state as { message?: string } | null)?.message;
    if (message) {
      setFlashMessage(message);
      window.history.replaceState({}, "");
    }
  }, [location.state]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <h2 className="text-xl font-semibold text-zinc-100">Файлы</h2>
      {flashMessage && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          {flashMessage}
        </div>
      )}
      <div className="min-h-0 flex-1">
        <FileManager apiPrefix="/api/files" mode="plain" />
      </div>
    </div>
  );
}
