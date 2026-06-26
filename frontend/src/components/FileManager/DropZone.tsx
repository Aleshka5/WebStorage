import { useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";

interface DropZoneProps {
  onDrop: (files: File[]) => void;
  disabled?: boolean;
  children: ReactNode;
}

export function DropZone({ onDrop, disabled = false, children }: DropZoneProps) {
  const [isDragging, setIsDragging] = useState(false);
  const dragCounterRef = useRef(0);

  const handleDragEnter = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();

    if (disabled) {
      return;
    }

    dragCounterRef.current += 1;
    setIsDragging(true);
  };

  const handleDragLeave = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();

    dragCounterRef.current -= 1;

    if (dragCounterRef.current <= 0) {
      dragCounterRef.current = 0;
      setIsDragging(false);
    }
  };

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();

    dragCounterRef.current = 0;
    setIsDragging(false);

    if (disabled) {
      return;
    }

    const droppedFiles = Array.from(event.dataTransfer.files);

    if (droppedFiles.length > 0) {
      onDrop(droppedFiles);
    }
  };

  useEffect(() => {
    if (disabled) {
      dragCounterRef.current = 0;
      setIsDragging(false);
    }
  }, [disabled]);

  return (
    <div
      className="relative min-h-[320px] flex-1"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {children}
      {isDragging && !disabled && (
        <div
          className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-xl border-2 border-dashed border-sky-400 bg-sky-500/10 backdrop-blur-[1px]"
          aria-hidden="true"
        >
          <p className="rounded-lg bg-zinc-900/90 px-4 py-2 text-sm font-medium text-sky-300">
            Отпустите файлы для загрузки
          </p>
        </div>
      )}
    </div>
  );
}
