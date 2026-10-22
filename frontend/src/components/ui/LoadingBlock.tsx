import { useEffect, useState } from "react";

const SKELETON_DELAY_MS = 120;

export function useDelayedFlag(active: boolean, delayMs = SKELETON_DELAY_MS): boolean {
  const [shown, setShown] = useState(false);

  useEffect(() => {
    if (!active) {
      setShown(false);
      return;
    }

    const timer = window.setTimeout(() => {
      setShown(true);
    }, delayMs);

    return () => {
      window.clearTimeout(timer);
    };
  }, [active, delayMs]);

  return active && shown;
}

interface LoadingBlockProps {
  active: boolean;
  label: string;
}

export function LoadingBlock({ active, label }: LoadingBlockProps) {
  const show = useDelayedFlag(active);

  if (!active) {
    return null;
  }

  if (!show) {
    return (
      <p className="sr-only" aria-live="polite">
        {label}
      </p>
    );
  }

  return (
    <div aria-busy="true" aria-live="polite">
      <p className="empty">{label}</p>
      <div className="skeleton" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
    </div>
  );
}
