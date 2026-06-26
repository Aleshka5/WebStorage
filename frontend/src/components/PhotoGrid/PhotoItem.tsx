import { Check } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { PhotoItem } from "../../types/photos";

const LONG_PRESS_MS = 500;

interface PhotoItemProps {
  photo: PhotoItem;
  isSelected: boolean;
  selectionMode: boolean;
  onOpen: (photo: PhotoItem) => void;
  onToggleSelect: (photoId: string) => void;
  onEnterSelectionMode: () => void;
}

export function PhotoItemComponent({
  photo,
  isSelected,
  selectionMode,
  onOpen,
  onToggleSelect,
  onEnterSelectionMode,
}: PhotoItemProps) {
  const [isLoaded, setIsLoaded] = useState(false);
  const longPressTimerRef = useRef<number | null>(null);
  const longPressTriggeredRef = useRef(false);

  const clearLongPressTimer = useCallback(() => {
    if (longPressTimerRef.current !== null) {
      window.clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
  }, []);

  useEffect(() => clearLongPressTimer, [clearLongPressTimer]);

  const handlePointerDown = () => {
    longPressTriggeredRef.current = false;
    clearLongPressTimer();

    longPressTimerRef.current = window.setTimeout(() => {
      longPressTriggeredRef.current = true;
      onEnterSelectionMode();
      onToggleSelect(photo.id);
    }, LONG_PRESS_MS);
  };

  const handlePointerUp = () => {
    clearLongPressTimer();
  };

  const handleClick = () => {
    if (longPressTriggeredRef.current) {
      longPressTriggeredRef.current = false;
      return;
    }

    if (selectionMode) {
      onToggleSelect(photo.id);
      return;
    }

    onOpen(photo);
  };

  const handleCheckboxClick = (event: React.MouseEvent) => {
    event.stopPropagation();

    if (!selectionMode) {
      onEnterSelectionMode();
    }

    onToggleSelect(photo.id);
  };

  return (
    <button
      type="button"
      className={[
        "group relative aspect-square w-full overflow-hidden rounded-md bg-zinc-900",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500",
        isSelected ? "ring-2 ring-sky-500" : "",
      ].join(" ")}
      onClick={handleClick}
      onPointerDown={handlePointerDown}
      onPointerUp={handlePointerUp}
      onPointerLeave={handlePointerUp}
      onPointerCancel={handlePointerUp}
      onContextMenu={(event) => event.preventDefault()}
      aria-label={photo.id}
      aria-pressed={selectionMode ? isSelected : undefined}
    >
      {!isLoaded && (
        <div
          className="absolute inset-0 animate-pulse bg-zinc-800"
          aria-hidden="true"
        />
      )}

      <img
        src={photo.preview_url}
        alt=""
        loading="lazy"
        className={[
          "h-full w-full object-cover transition-opacity duration-300",
          isLoaded ? "opacity-100" : "opacity-0",
        ].join(" ")}
        onLoad={() => setIsLoaded(true)}
        draggable={false}
      />

      <div
        className={[
          "absolute left-2 top-2 transition-opacity",
          selectionMode ? "opacity-100" : "opacity-0 group-hover:opacity-100",
        ].join(" ")}
      >
        <span
          role="checkbox"
          aria-checked={isSelected}
          tabIndex={-1}
          onClick={handleCheckboxClick}
          className={[
            "flex h-6 w-6 items-center justify-center rounded-full border-2 transition-colors",
            isSelected
              ? "border-sky-500 bg-sky-500 text-white"
              : "border-white/80 bg-black/40 text-transparent hover:border-sky-400",
          ].join(" ")}
        >
          <Check className="h-3.5 w-3.5" aria-hidden="true" />
        </span>
      </div>

      {selectionMode && isSelected && (
        <div className="pointer-events-none absolute inset-0 bg-sky-500/20" aria-hidden="true" />
      )}
    </button>
  );
}
