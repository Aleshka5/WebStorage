import { useState } from "react";
import { useAuthStore } from "../../store/auth";
import { displayName, hubHref, signInHref } from "../../utils/hub";

export function Header() {
  const user = useAuthStore((state) => state.user);
  const sessionChecked = useAuthStore((state) => state.sessionChecked);
  const [signingOut, setSigningOut] = useState(false);

  const isGuest = sessionChecked && user === null;

  return (
    <header className="header">
      <a className="brand" href={hubHref()}>
        <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
          <path
            d="M16 3 L29 16 L16 29 L3 16 Z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.75"
          />
          <circle cx="16" cy="16" r="3.2" fill="var(--accent)" />
        </svg>
        <span className="brand-name">Filenkov</span>
      </a>
      <nav className="service-nav" aria-label="Primary">
        <span aria-current="page">
          Storage
          {isGuest ? (
            <span className="service-lock" aria-hidden="true">
              🔒
            </span>
          ) : null}
        </span>
      </nav>
      <div className="header-right">
        {!sessionChecked ? <span className="muted">Checking sign-in…</span> : null}
        {isGuest ? (
          <a className="btn btn-small" href={signInHref()}>
            Sign in
          </a>
        ) : null}
        {user ? (
          <>
            <span className="user-name">{displayName(user.email)}</span>
            <button
              type="button"
              className="btn btn-ghost btn-small"
              disabled={signingOut}
              onClick={() => {
                setSigningOut(true);
                window.location.assign(hubHref());
              }}
            >
              Sign out
            </button>
          </>
        ) : null}
      </div>
    </header>
  );
}
