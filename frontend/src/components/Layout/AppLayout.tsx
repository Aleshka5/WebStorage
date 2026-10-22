import type { ReactNode } from "react";
import { Outlet } from "react-router-dom";
import { useAuthStore } from "../../store/auth";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";

interface ShellProps {
  children: ReactNode;
  showNavigation?: boolean;
}

export function Shell({ children, showNavigation = false }: ShellProps) {
  const user = useAuthStore((state) => state.user);

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Header />
      <div className="workspace">
        {showNavigation && user ? <Sidebar /> : null}
        <main className="main" id="main">
          {children}
        </main>
      </div>
      <footer className="footer">
        <span>Filenkov · Storage</span>
        <span>Google sign-in · httpOnly cookies on .filenkov.store</span>
      </footer>
      <p id="route-announcer" className="sr-only" aria-live="polite" />
    </div>
  );
}

export function AppLayout() {
  return (
    <Shell showNavigation>
      <Outlet />
    </Shell>
  );
}
