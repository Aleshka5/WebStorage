import type { RefObject } from "react";
import { LoadingBlock } from "../ui/LoadingBlock";
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
        <p className="empty">No photos yet. Use Upload to add some.</p>
      )}

      {isLoading && <LoadingBlock active label="Loading photos…" />}

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
