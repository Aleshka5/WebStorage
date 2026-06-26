import { useCallback, useEffect, useRef, type Dispatch, type SetStateAction } from "react";

export function useInfiniteScroll(
  page: number,
  _setPage: Dispatch<SetStateAction<number>>,
  hasNext: boolean,
  isLoading: boolean,
  loadFn: () => Promise<void>,
) {
  const sentinelRef = useRef<HTMLDivElement>(null);
  const loadingRef = useRef(false);

  const loadMore = useCallback(async () => {
    if (!hasNext || isLoading || loadingRef.current) {
      return;
    }

    loadingRef.current = true;

    try {
      await loadFn();
    } finally {
      loadingRef.current = false;
    }
  }, [hasNext, isLoading, loadFn]);

  useEffect(() => {
    const sentinel = sentinelRef.current;

    if (!sentinel) {
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          void loadMore();
        }
      },
      { rootMargin: "300px" },
    );

    observer.observe(sentinel);

    return () => observer.disconnect();
  }, [loadMore, page]);

  return { sentinelRef, loadMore };
}
