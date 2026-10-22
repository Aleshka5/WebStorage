import { useEffect, useRef, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

interface PageFrameProps {
  title: string;
  kicker?: string;
  lead?: string;
  actions?: ReactNode;
  children: ReactNode;
}

let hasSettledFirstView = false;

export function PageFrame({
  title,
  kicker = "Storage",
  lead,
  actions,
  children,
}: PageFrameProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const location = useLocation();

  useEffect(() => {
    const documentTitle = `${title} · Storage · Filenkov`;
    document.title = documentTitle;

    const announcer = document.getElementById("route-announcer");
    if (announcer) {
      announcer.textContent = documentTitle;
    }

    const timer = window.setTimeout(() => {
      if (!hasSettledFirstView) {
        hasSettledFirstView = true;
        return;
      }

      headingRef.current?.focus();
    }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [location.pathname, title]);

  return (
    <div className="view-in tool">
      <div className="page-head">
        <div>
          <p className="kicker">{kicker}</p>
          <h1 tabIndex={-1} ref={headingRef}>
            {title}
          </h1>
        </div>
        {actions}
      </div>
      {lead ? <p className="muted page-lead">{lead}</p> : null}
      {children}
    </div>
  );
}
