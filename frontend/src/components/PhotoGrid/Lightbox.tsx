import { ChevronLeft, ChevronRight, Download, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { downloadPhotoOriginal } from "../../services/photosApi";
import type { PhotoItem } from "../../types/photos";

interface LightboxProps {
  photos: PhotoItem[];
  currentIndex: number;
  onClose: () => void;
  onNavigate: (index: number) => void;
}

export function Lightbox({ photos, currentIndex, onClose, onNavigate }: LightboxProps) {
  const photo = photos[currentIndex];
  const [isImageLoaded, setIsImageLoaded] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);

  const hasPrevious = currentIndex > 0;
  const hasNext = currentIndex < photos.length - 1;

  const handlePrevious = useCallback(() => {
    if (hasPrevious) {
      onNavigate(currentIndex - 1);
    }
  }, [currentIndex, hasPrevious, onNavigate]);

  const handleNext = useCallback(() => {
    if (hasNext) {
      onNavigate(currentIndex + 1);
    }
  }, [currentIndex, hasNext, onNavigate]);

  const handleDownload = useCallback(async () => {
    if (!photo || isDownloading) {
      return;
    }

    setIsDownloading(true);

    try {
      await downloadPhotoOriginal(photo.original_url, `photo-${photo.id}.jpg`);
    } finally {
      setIsDownloading(false);
    }
  }, [isDownloading, photo]);

  useEffect(() => {
    setIsImageLoaded(false);
  }, [photo?.id]);

  useEffect(() => {
    document.body.style.overflow = "hidden";

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      } else if (event.key === "ArrowLeft") {
        handlePrevious();
      } else if (event.key === "ArrowRight") {
        handleNext();
      }
    };

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [handleNext, handlePrevious, onClose]);

  if (!photo) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/95"
      role="dialog"
      aria-modal="true"
      aria-label="Просмотр фото"
    >
      <button
        type="button"
        className="absolute right-4 top-4 z-10 rounded-full bg-black/50 p-2 text-zinc-200 transition-colors hover:bg-black/70 hover:text-white"
        onClick={onClose}
        aria-label="Закрыть"
      >
        <X className="h-6 w-6" />
      </button>

      {hasPrevious && (
        <button
          type="button"
          className="absolute left-4 z-10 rounded-full bg-black/50 p-3 text-zinc-200 transition-colors hover:bg-black/70 hover:text-white"
          onClick={handlePrevious}
          aria-label="Предыдущее фото"
        >
          <ChevronLeft className="h-8 w-8" />
        </button>
      )}

      {hasNext && (
        <button
          type="button"
          className="absolute right-4 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/50 p-3 text-zinc-200 transition-colors hover:bg-black/70 hover:text-white"
          onClick={handleNext}
          aria-label="Следующее фото"
        >
          <ChevronRight className="h-8 w-8" />
        </button>
      )}

      <button
        type="button"
        className="absolute bottom-4 right-4 z-10 rounded-full bg-black/50 p-3 text-zinc-200 transition-colors hover:bg-black/70 hover:text-white disabled:opacity-50"
        onClick={() => void handleDownload()}
        disabled={isDownloading}
        aria-label="Скачать"
      >
        {isDownloading ? (
          <span
            className="block h-6 w-6 animate-spin rounded-full border-2 border-current border-t-transparent"
            aria-label="Загрузка"
          />
        ) : (
          <Download className="h-6 w-6" />
        )}
      </button>

      <div className="flex h-full w-full items-center justify-center p-4 pt-16 pb-20">
        {!isImageLoaded && (
          <div
            className="absolute h-64 w-64 max-w-full animate-pulse rounded-lg bg-zinc-800"
            aria-hidden="true"
          />
        )}

        <img
          key={photo.id}
          src={photo.original_url}
          alt=""
          loading="lazy"
          className={[
            "max-h-full max-w-full object-contain transition-opacity duration-300",
            isImageLoaded ? "opacity-100" : "opacity-0",
          ].join(" ")}
          onLoad={() => setIsImageLoaded(true)}
          draggable={false}
        />
      </div>

      <p className="absolute bottom-4 left-4 text-sm text-zinc-400">
        {currentIndex + 1} / {photos.length}
      </p>
    </div>
  );
}
