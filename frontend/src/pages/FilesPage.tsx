import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { FileManager } from "../components/FileManager/FileManager";
import { PageFrame } from "../components/Layout/PageFrame";

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
    <PageFrame title="Files" lead="Folders and files in this account.">
      {flashMessage && <div className="banner">{flashMessage}</div>}
      <FileManager apiPrefix="/api/files" mode="plain" />
    </PageFrame>
  );
}
