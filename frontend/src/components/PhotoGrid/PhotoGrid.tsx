import type { RefObject } from "react";
import type { PhotoItem } from "../../types/photos";
import { Lightbox } from "./Lightbox";
import { PhotoItemComponent } from "./PhotoItem";

interface PhotoGridProps {
  photos: PhotoItem[];
  isLoading: boolean;
  hasNext: boolean;
  sentinelRef: RefObject<HTMLDivElement>;
  selectionMode: boolean;
  selectedIds: Set<string>;
  lightboxIndex: number | null;
  onOpenLightbox: (photo: PhotoItem) => void;
  onCloseLightbox: () => void;
  onNavigateLightbox: (index: number) => void;
  onEnterSelectionMode: () => void;
  onToggleSelect: (photoId: string) => void;
}

export function PhotoGrid({
  photos,
  isLoading,
  hasNext,
  sentinelRef,
  selectionMode,
  selectedIds,
  lightboxIndex,
  onOpenLightbox,
  onCloseLightbox,
  onNavigateLightbox,
  onEnterSelectionMode,
  onToggleSelect,
}: PhotoGridProps) {
  return (
    <>
      <div className="photo-grid">
        {photos.map((photo) => (
          <PhotoItemComponent
            key={photo.id}
            photo={photo}
            isSelected={selectedIds.has(photo.id)}
            selectionMode={selectionMode}
            onOpen={onOpenLightbox}
            onToggleSelect={onToggleSelect}
            onEnterSelectionMode={onEnterSelectionMode}
          />
        ))}
      </div>

      {photos.length === 0 && !isLoading && (
        <p className="py-12 text-center text-zinc-500">
          Нет фотографий. Нажмите «+», чтобы загрузить.
        </p>
      )}

      {isLoading && (
        <div className="flex justify-center py-6" aria-live="polite">
          <span
            className="h-8 w-8 animate-spin rounded-full border-2 border-zinc-600 border-t-sky-500"
            aria-label="Загрузка"
          />
        </div>
      )}

      {hasNext && !isLoading && photos.length > 0 && (
        <div ref={sentinelRef} className="h-1 w-full" aria-hidden="true" />
      )}

      {lightboxIndex !== null && (
        <Lightbox
          photos={photos}
          currentIndex={lightboxIndex}
          onClose={onCloseLightbox}
          onNavigate={onNavigateLightbox}
        />
      )}
    </>
  );
}
