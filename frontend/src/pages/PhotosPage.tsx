import { Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { PhotoGrid } from "../components/PhotoGrid/PhotoGrid";
import { PhotoUploadFab } from "../components/PhotoGrid/PhotoUploadFab";
import { Button } from "../components/ui/Button";
import { ErrorMessage } from "../components/ui/ErrorMessage";
import { Modal } from "../components/ui/Modal";
import { useInfiniteScroll } from "../hooks/useInfiniteScroll";
import { usePhotoUpload } from "../hooks/usePhotoUpload";
import { deletePhoto, listPhotos } from "../services/photosApi";
import { useQuotaStore } from "../store/quota";
import type { PhotoItem } from "../types/photos";

export default function PhotosPage() {
  const [photos, setPhotos] = useState<PhotoItem[]>([]);
  const [page, setPage] = useState(1);
  const [hasNext, setHasNext] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [listErrorCode, setListErrorCode] = useState<string | null>(null);

  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  const savedScrollRef = useRef(0);

  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const { uploads, uploadPhotos, clearFinished } = usePhotoUpload();
  const fetchQuota = useQuotaStore((state) => state.fetchQuota);

  const fetchPhotos = useCallback(async (pageNum: number, append: boolean) => {
    setIsLoading(true);
    setListErrorCode(null);

    try {
      const data = await listPhotos(pageNum);
      setPhotos((current) => (append ? [...current, ...data.items] : data.items));
      setHasNext(data.has_next);
      setPage(pageNum);
    } catch {
      setListErrorCode("INTERNAL_ERROR");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadMore = useCallback(async () => {
    if (!hasNext || isLoading) {
      return;
    }

    await fetchPhotos(page + 1, true);
  }, [fetchPhotos, hasNext, isLoading, page]);

  const { sentinelRef } = useInfiniteScroll(page, setPage, hasNext, isLoading, loadMore);

  useEffect(() => {
    void fetchPhotos(1, false);
  }, [fetchPhotos]);

  const handleOpenLightbox = useCallback(
    (photo: PhotoItem) => {
      const index = photos.findIndex((item) => item.id === photo.id);

      if (index === -1) {
        return;
      }

      const main = document.querySelector("main");
      savedScrollRef.current = main?.scrollTop ?? window.scrollY;
      setLightboxIndex(index);
    },
    [photos],
  );

  const handleCloseLightbox = useCallback(() => {
    setLightboxIndex(null);

    requestAnimationFrame(() => {
      const main = document.querySelector("main");

      if (main) {
        main.scrollTop = savedScrollRef.current;
      } else {
        window.scrollTo(0, savedScrollRef.current);
      }
    });
  }, []);

  const handleEnterSelectionMode = useCallback(() => {
    setSelectionMode(true);
  }, []);

  const handleToggleSelect = useCallback((photoId: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);

      if (next.has(photoId)) {
        next.delete(photoId);
      } else {
        next.add(photoId);
      }

      return next;
    });
  }, []);

  const handleExitSelectionMode = useCallback(() => {
    setSelectionMode(false);
    setSelectedIds(new Set());
  }, []);

  const handleSelectAll = useCallback(() => {
    setSelectedIds(new Set(photos.map((photo) => photo.id)));
  }, [photos]);

  const allPhotosSelected =
    photos.length > 0 && photos.every((photo) => selectedIds.has(photo.id));

  const handleUpload = useCallback(
    (files: File[]) => {
      void uploadPhotos(files, (photo) => {
        setPhotos((current) => {
          if (current.some((item) => item.id === photo.id)) {
            return current;
          }

          return [photo, ...current];
        });
        void fetchQuota();
      });
    },
    [fetchQuota, uploadPhotos],
  );

  const handleConfirmDelete = useCallback(async () => {
    const idsToDelete = Array.from(selectedIds);

    if (idsToDelete.length === 0) {
      return;
    }

    setIsDeleting(true);

    try {
      await Promise.all(idsToDelete.map((id) => deletePhoto(id)));
      setPhotos((current) => current.filter((photo) => !selectedIds.has(photo.id)));
      handleExitSelectionMode();
      setIsDeleteModalOpen(false);
      void fetchQuota();
    } catch {
      setListErrorCode("INTERNAL_ERROR");
    } finally {
      setIsDeleting(false);
    }
  }, [fetchQuota, handleExitSelectionMode, selectedIds]);

  useEffect(() => {
    if (selectionMode && selectedIds.size === 0) {
      setSelectionMode(false);
    }
  }, [selectedIds.size, selectionMode]);

  return (
    <div className="flex min-h-0 flex-col gap-4">
      <h2 className="text-xl font-semibold text-zinc-100">Фото</h2>

      {selectionMode && (
        <div
          className={[
            "sticky top-0 z-30 -mx-6 flex flex-wrap items-center justify-between gap-3",
            "border-b border-zinc-800 bg-zinc-950/95 px-6 py-3 backdrop-blur-sm",
          ].join(" ")}
        >
          <span className="text-sm font-medium text-zinc-200">
            Выбрано: {selectedIds.size}
          </span>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="secondary"
              className="w-auto px-3"
              disabled={allPhotosSelected}
              onClick={handleSelectAll}
            >
              Выбрать все
            </Button>
            <Button
              type="button"
              variant="danger"
              className="w-auto px-3"
              disabled={selectedIds.size === 0}
              onClick={() => setIsDeleteModalOpen(true)}
            >
              <span className="inline-flex items-center gap-2">
                <Trash2 className="h-4 w-4" />
                Удалить
              </span>
            </Button>
            <button
              type="button"
              onClick={handleExitSelectionMode}
              className="rounded p-2 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
              aria-label="Отменить выбор"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>
      )}

      {listErrorCode && <ErrorMessage errorCode={listErrorCode} />}

      <PhotoGrid
        photos={photos}
        isLoading={isLoading}
        hasNext={hasNext}
        sentinelRef={sentinelRef}
        selectionMode={selectionMode}
        selectedIds={selectedIds}
        lightboxIndex={lightboxIndex}
        onOpenLightbox={handleOpenLightbox}
        onCloseLightbox={handleCloseLightbox}
        onNavigateLightbox={setLightboxIndex}
        onEnterSelectionMode={handleEnterSelectionMode}
        onToggleSelect={handleToggleSelect}
      />

      <PhotoUploadFab
        uploads={uploads}
        onFilesSelected={handleUpload}
        onClearFinished={clearFinished}
      />

      <Modal
        isOpen={isDeleteModalOpen}
        onClose={() => {
          if (!isDeleting) {
            setIsDeleteModalOpen(false);
          }
        }}
        title="Удалить фото?"
      >
        <p className="mb-4 text-sm text-zinc-300">
          {selectedIds.size === 1
            ? "Выбранное фото будет удалено без возможности восстановления."
            : `Выбранные фото (${selectedIds.size}) будут удалены без возможности восстановления.`}
        </p>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="secondary"
            onClick={() => setIsDeleteModalOpen(false)}
            disabled={isDeleting}
          >
            Отмена
          </Button>
          <Button
            type="button"
            variant="danger"
            isLoading={isDeleting}
            onClick={() => {
              void handleConfirmDelete();
            }}
          >
            Удалить
          </Button>
        </div>
      </Modal>
    </div>
  );
}
