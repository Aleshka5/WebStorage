import { Plus, X } from "lucide-react";
import { useCallback, useRef } from "react";
import type { PhotoUploadState } from "../../hooks/usePhotoUpload";
import { ErrorMessage } from "../ui/ErrorMessage";

interface PhotoUploadFabProps {
  uploads: PhotoUploadState[];
  onFilesSelected: (files: File[]) => void;
  onClearFinished: () => void;
}

export function PhotoUploadFab({
  uploads,
  onFilesSelected,
  onClearFinished,
}: PhotoUploadFabProps) {
  const isProcessingRef = useRef(false);

  const handleFiles = useCallback(
    (input: HTMLInputElement) => {
      const selectedFiles = input.files ? Array.from(input.files) : [];

      if (selectedFiles.length === 0 || isProcessingRef.current) {
        return;
      }

      isProcessingRef.current = true;
      onFilesSelected(selectedFiles);

      // Сброс input после завершения обработки (iOS чувствителен к раннему reset).
      window.setTimeout(() => {
        input.value = "";
        isProcessingRef.current = false;
      }, 500);
    },
    [onFilesSelected],
  );

  const handleInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      handleFiles(event.currentTarget);
    },
    [handleFiles],
  );

  return (
    <>
      <div className="upload-fab">
        <label className="btn" aria-label="Upload photos">
          <input
            type="file"
            accept="image/*"
            multiple
            onChange={handleInputChange}
            onInput={handleInputChange}
          />
          <Plus size={18} aria-hidden="true" />
        </label>
      </div>

      {uploads.length > 0 && (
        <div className="upload-dock surface">
          <div className="toolbar">
            <p>Uploading photos</p>
            <button type="button" onClick={onClearFinished} className="icon-btn" aria-label="Hide completed">
              <X size={16} />
            </button>
          </div>
          <ul className="stack-tight">
            {uploads.map((upload) => (
              <li key={upload.id}>
                <div className="toolbar">
                  <span>{upload.name}</span>
                  <span className="muted nowrap">
                    {upload.status === "uploading" &&
                      (upload.progress >= 100 ? "Saving…" : `${upload.progress}%`)}
                    {upload.status === "done" && "Done"}
                    {upload.status === "error" && "Error"}
                  </span>
                </div>
                {upload.status === "uploading" && (
                  <div className="meter" aria-hidden="true">
                    <span style={{ width: `${upload.progress}%` }} />
                  </div>
                )}
                {upload.status === "error" && <ErrorMessage errorCode={upload.error_code} />}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
