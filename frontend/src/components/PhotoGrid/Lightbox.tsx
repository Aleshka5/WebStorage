import { ChevronLeft, ChevronRight, Download, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { downloadPhotoOriginal } from "../../services/photosApi";
import type { PhotoItem } from "../../types/photos";
import { ViewportPortal } from "../ui/ViewportPortal";

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
    <ViewportPortal>
      <div className="lightbox" role="dialog" aria-modal="true" aria-label="Photo viewer">
        <button type="button" className="overlay-dismiss" aria-label="Close" onClick={onClose} />

        <div className="lightbox-bar top">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            <X size={18} aria-hidden="true" />
            Close
          </button>
        </div>

        {hasPrevious && (
          <button
            type="button"
            className="btn btn-ghost lightbox-nav prev"
            onClick={handlePrevious}
            aria-label="Previous photo"
          >
            <ChevronLeft size={18} aria-hidden="true" />
          </button>
        )}

        {hasNext && (
          <button
            type="button"
            className="btn btn-ghost lightbox-nav next"
            onClick={handleNext}
            aria-label="Next photo"
          >
            <ChevronRight size={18} aria-hidden="true" />
          </button>
        )}

        <figure className="lightbox-figure">
          {!isImageLoaded && (
            <div className="skeleton" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
          )}
          <img
            key={photo.id}
            src={photo.original_url}
            alt=""
            loading="lazy"
            decoding="async"
            style={{ opacity: isImageLoaded ? 1 : 0 }}
            onLoad={() => setIsImageLoaded(true)}
            draggable={false}
          />
        </figure>

        <div className="lightbox-bar bottom">
          <p className="muted">
            {currentIndex + 1} / {photos.length}
          </p>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => void handleDownload()}
            disabled={isDownloading}
          >
            <Download size={18} aria-hidden="true" />
            {isDownloading ? "Downloading…" : "Download"}
          </button>
        </div>
      </div>
    </ViewportPortal>
  );
}
