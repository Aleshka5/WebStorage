import { Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { PageFrame } from "../components/Layout/PageFrame";
import { PhotoGrid } from "../components/PhotoGrid/PhotoGrid";
import { PhotoSearchBar } from "../components/PhotoGrid/PhotoSearchBar";
import { PhotoUploadFab } from "../components/PhotoGrid/PhotoUploadFab";
import { Button } from "../components/ui/Button";
import { ErrorMessage } from "../components/ui/ErrorMessage";
import { LoadingBlock } from "../components/ui/LoadingBlock";
import { Modal } from "../components/ui/Modal";
import { useInfiniteScroll } from "../hooks/useInfiniteScroll";
import { usePhotoUpload } from "../hooks/usePhotoUpload";
import { getApiErrorDetail } from "../services/api";
import { deletePhoto, listPhotos, searchPhotos } from "../services/photosApi";
import { useQuotaStore } from "../store/quota";
import type { PhotoItem } from "../types/photos";
import { PHOTO_SEARCH_QUERY_MAX_LENGTH } from "../types/photos";

const SEARCH_UNAVAILABLE_CODE = "INDEXING_UNAVAILABLE";
const SEARCH_QUERY_INVALID_CODE = "SEARCH_QUERY_INVALID";
const SEARCH_UNAVAILABLE_MESSAGE = "Search is unavailable right now.";
const SEARCH_EMPTY_MESSAGE = "No photos match.";
const SEARCH_LOADING_LABEL = "Searching photos…";

export default function PhotosPage() {
  const [photos, setPhotos] = useState<PhotoItem[]>([]);
  const [page, setPage] = useState(1);
  const [hasNext, setHasNext] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [listErrorCode, setListErrorCode] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [activeQuery, setActiveQuery] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<PhotoItem[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [searchErrorCode, setSearchErrorCode] = useState<string | null>(null);

  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  const savedScrollRef = useRef(0);

  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const activeQueryRef = useRef<string | null>(null);
  const isSearchingRef = useRef(false);
  const searchRequestRef = useRef(0);

  const { uploads, uploadPhotos, clearFinished } = usePhotoUpload();
  const fetchQuota = useQuotaStore((state) => state.fetchQuota);

  const showingSearchResults = activeQuery !== null && !isSearching;
  const displayedPhotos = showingSearchResults ? searchResults : photos;

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

  const { sentinelRef } = useInfiniteScroll(
    page,
    setPage,
    !showingSearchResults && !isSearching && hasNext,
    isLoading || isSearching,
    loadMore,
  );

  useEffect(() => {
    void fetchPhotos(1, false);
  }, [fetchPhotos]);

  const restorePhotoList = useCallback(() => {
    const wasSearching = activeQueryRef.current !== null || isSearchingRef.current;

    searchRequestRef.current += 1;
    activeQueryRef.current = null;
    isSearchingRef.current = false;
    setIsSearching(false);
    setSearchErrorCode(null);

    if (!wasSearching) {
      return;
    }

    setActiveQuery(null);
    setSearchResults([]);
    setLightboxIndex(null);
    setSelectionMode(false);
    setSelectedIds(new Set());
    void fetchPhotos(1, false);
  }, [fetchPhotos]);

  const handleQueryChange = useCallback(
    (value: string) => {
      setQuery(value);

      if (value.trim() === "") {
        restorePhotoList();
      }
    },
    [restorePhotoList],
  );

  const handleClearSearch = useCallback(() => {
    setQuery("");
    restorePhotoList();
  }, [restorePhotoList]);

  const handleSearchSubmit = useCallback(
    (rawQuery: string) => {
      const trimmed = rawQuery.trim();

      if (trimmed === "") {
        setQuery("");
        restorePhotoList();
        return;
      }

      if (trimmed.length > PHOTO_SEARCH_QUERY_MAX_LENGTH) {
        searchRequestRef.current += 1;
        activeQueryRef.current = null;
        isSearchingRef.current = false;
        setActiveQuery(null);
        setSearchResults([]);
        setIsSearching(false);
        setSearchErrorCode(SEARCH_QUERY_INVALID_CODE);
        return;
      }

      const requestId = searchRequestRef.current + 1;
      searchRequestRef.current = requestId;
      activeQueryRef.current = trimmed;
      isSearchingRef.current = true;
      setActiveQuery(trimmed);
      setIsSearching(true);
      setSearchErrorCode(null);
      setSearchResults([]);
      setLightboxIndex(null);
      setSelectionMode(false);
      setSelectedIds(new Set());

      void (async () => {
        try {
          const data = await searchPhotos(trimmed);

          if (searchRequestRef.current !== requestId) {
            return;
          }

          isSearchingRef.current = false;
          setSearchResults(data.items);
          setIsSearching(false);
        } catch (error) {
          if (searchRequestRef.current !== requestId) {
            return;
          }

          activeQueryRef.current = null;
          isSearchingRef.current = false;
          setActiveQuery(null);
          setSearchResults([]);
          setIsSearching(false);
          setSearchErrorCode(getApiErrorDetail(error)?.error_code ?? "INTERNAL_ERROR");
        }
      })();
    },
    [restorePhotoList],
  );

  const handleOpenLightbox = useCallback(
    (photo: PhotoItem) => {
      const index = displayedPhotos.findIndex((item) => item.id === photo.id);

      if (index === -1) {
        return;
      }

      const main = document.querySelector("main");
      savedScrollRef.current = main?.scrollTop ?? window.scrollY;
      setLightboxIndex(index);
    },
    [displayedPhotos],
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
    setSelectedIds(new Set(displayedPhotos.map((photo) => photo.id)));
  }, [displayedPhotos]);

  const allPhotosSelected =
    displayedPhotos.length > 0 && displayedPhotos.every((photo) => selectedIds.has(photo.id));

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
      const removeDeleted = (current: PhotoItem[]) =>
        current.filter((photo) => !selectedIds.has(photo.id));
      setPhotos(removeDeleted);
      setSearchResults(removeDeleted);
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
    <PageFrame title="Photos" lead="Pictures in this account, newest first.">
      <PhotoSearchBar
        query={query}
        isSearching={isSearching}
        onQueryChange={handleQueryChange}
        onSubmit={handleSearchSubmit}
        onClear={handleClearSearch}
      />

      {selectionMode && (
        <div className="selection-bar">
          <span>Selected: {selectedIds.size}</span>
          <div className="actions">
            <Button type="button" variant="secondary" disabled={allPhotosSelected} onClick={handleSelectAll}>
              Select all
            </Button>
            <Button
              type="button"
              variant="danger"
              disabled={selectedIds.size === 0}
              onClick={() => setIsDeleteModalOpen(true)}
            >
              <Trash2 size={16} aria-hidden="true" />
              Delete
            </Button>
            <button type="button" onClick={handleExitSelectionMode} className="icon-btn" aria-label="Cancel selection">
              <X size={18} />
            </button>
          </div>
        </div>
      )}

      {listErrorCode && <ErrorMessage errorCode={listErrorCode} />}
      {searchErrorCode === SEARCH_UNAVAILABLE_CODE && (
        <ErrorMessage message={SEARCH_UNAVAILABLE_MESSAGE} />
      )}
      {searchErrorCode && searchErrorCode !== SEARCH_UNAVAILABLE_CODE && (
        <ErrorMessage errorCode={searchErrorCode} />
      )}

      {isSearching ? (
        <LoadingBlock active label={SEARCH_LOADING_LABEL} />
      ) : showingSearchResults && searchResults.length === 0 ? (
        <p className="empty">{SEARCH_EMPTY_MESSAGE}</p>
      ) : (
        <PhotoGrid
          photos={displayedPhotos}
          isLoading={showingSearchResults ? false : isLoading}
          hasNext={showingSearchResults ? false : hasNext}
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
      )}

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
        title="Delete photos?"
      >
        <p>
          {selectedIds.size === 1
            ? "The selected photo will be permanently deleted."
            : `The selected photos (${selectedIds.size}) will be permanently deleted.`}
        </p>
        <div className="actions">
          <Button
            type="button"
            variant="secondary"
            onClick={() => setIsDeleteModalOpen(false)}
            disabled={isDeleting}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="danger"
            isLoading={isDeleting}
            onClick={() => {
              void handleConfirmDelete();
            }}
          >
            Delete
          </Button>
        </div>
      </Modal>
    </PageFrame>
  );
}
