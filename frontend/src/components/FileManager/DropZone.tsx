import { useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";

interface DropZoneProps {
  onDrop: (files: File[]) => void;
  onDropZip?: (zipFiles: File[]) => void;
  disabled?: boolean;
  children: ReactNode;
}

export function DropZone({ onDrop, onDropZip, disabled = false, children }: DropZoneProps) {
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

    if (droppedFiles.length === 0) {
      return;
    }

    // Check if all dropped files are .zip files
    const zipFiles = droppedFiles.filter(
      (f) => f.name.toLowerCase().endsWith(".zip"),
    );

    if (zipFiles.length > 0 && onDropZip) {
      onDropZip(zipFiles);
    } else if (droppedFiles.length > 0) {
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
      className={["drop-zone", isDragging && !disabled ? "is-active" : ""].filter(Boolean).join(" ")}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {children}
    </div>
  );
}
