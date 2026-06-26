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
      <div className="fixed bottom-6 right-6 z-40">
        {/*
          iOS Safari: programmatic input.click() из отдельной кнопки часто не вызывает change.
          Input накладывается на FAB — пользователь тапает напрямую по input.
        */}
        <label
          className={[
            "relative flex h-14 w-14 cursor-pointer items-center justify-center overflow-hidden",
            "rounded-full bg-sky-600 text-white shadow-lg shadow-sky-900/40",
            "transition-colors hover:bg-sky-500 focus-within:outline-none focus-within:ring-2",
            "focus-within:ring-sky-500 focus-within:ring-offset-2 focus-within:ring-offset-zinc-950",
          ].join(" ")}
          aria-label="Загрузить фото"
        >
          <input
            type="file"
            accept="image/*"
            multiple
            className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
            onChange={handleInputChange}
            onInput={handleInputChange}
          />
          <Plus className="pointer-events-none h-7 w-7" aria-hidden="true" />
        </label>
      </div>

      {uploads.length > 0 && (
        <div className="fixed bottom-24 right-6 z-40 w-72 max-w-[calc(100vw-3rem)] rounded-xl border border-zinc-800 bg-zinc-900/95 p-3 shadow-xl backdrop-blur-sm">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-medium text-zinc-200">Загрузка фото</p>
            <button
              type="button"
              onClick={onClearFinished}
              className="rounded p-1 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-300"
              aria-label="Скрыть завершённые"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <ul className="max-h-48 space-y-2 overflow-y-auto">
            {uploads.map((upload) => (
              <li key={upload.id} className="text-sm">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="truncate text-zinc-300">{upload.name}</span>
                  <span className="shrink-0 text-xs text-zinc-500">
                    {upload.status === "uploading" && `${upload.progress}%`}
                    {upload.status === "done" && "Готово"}
                    {upload.status === "error" && "Ошибка"}
                  </span>
                </div>
                {upload.status === "uploading" && (
                  <div className="h-1.5 overflow-hidden rounded-full bg-zinc-800">
                    <div
                      className="h-full rounded-full bg-sky-500 transition-all"
                      style={{ width: `${upload.progress}%` }}
                    />
                  </div>
                )}
                {upload.status === "error" && (
                  <ErrorMessage errorCode={upload.error_code} className="text-xs" />
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
