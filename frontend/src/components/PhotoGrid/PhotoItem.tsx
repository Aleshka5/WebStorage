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
      className={["photo-tile", isSelected ? "is-selected" : ""].filter(Boolean).join(" ")}
      onClick={handleClick}
      onPointerDown={handlePointerDown}
      onPointerUp={handlePointerUp}
      onPointerLeave={handlePointerUp}
      onPointerCancel={handlePointerUp}
      onContextMenu={(event) => event.preventDefault()}
      aria-label={photo.id}
      aria-pressed={selectionMode ? isSelected : undefined}
    >
      {!isLoaded && <div className="photo-pending" aria-hidden="true" />}

      <img
        src={photo.preview_url}
        alt=""
        loading="lazy"
        decoding="async"
        style={{ opacity: isLoaded ? 1 : 0 }}
        onLoad={() => setIsLoaded(true)}
        draggable={false}
      />

      <span
        role="checkbox"
        aria-checked={isSelected}
        tabIndex={-1}
        onClick={handleCheckboxClick}
        className={["photo-check", isSelected ? "is-on" : ""].filter(Boolean).join(" ")}
      >
        <Check size={14} aria-hidden="true" />
      </span>
    </button>
  );
}
