import { useState, type ComponentType } from "react";
import { NavLink } from "react-router-dom";
import {
  Briefcase,
  Camera,
  ChevronLeft,
  ChevronRight,
  Folder,
  Key,
  Lock,
  Settings,
  Sparkles,
  Users,
} from "lucide-react";
import { useAuthStore } from "../../store/auth";
import { StorageUsageBar } from "./StorageUsageBar";

interface MenuItem {
  to: string;
  label: string;
  icon: ComponentType<{ className?: string; size?: number | string }>;
}

const MENU_ITEMS: MenuItem[] = [
  { to: "/photos", label: "Photos", icon: Camera },
  { to: "/generated", label: "Generated Images", icon: Sparkles },
  { to: "/files", label: "Files", icon: Folder },
  { to: "/private", label: "Private", icon: Lock },
  { to: "/keys", label: "Keys Registry", icon: Key },
  { to: "/resumes", label: "Resumes", icon: Briefcase },
  { to: "/shared", label: "Shared", icon: Users },
  { to: "/admin", label: "Admin", icon: Settings },
];

function isMenuItemVisible(to: string, role: string | undefined): boolean {
  if (to === "/shared" || to === "/resumes") {
    return role === "FAMILY" || role === "ADMIN";
  }

  if (to === "/admin") {
    return role === "ADMIN";
  }

  if (to === "/generated") {
    return role !== "BLOCKED";
  }

  return true;
}

export function Sidebar() {
  const user = useAuthStore((state) => state.user);
  const [expanded, setExpanded] = useState(true);

  const visibleItems = MENU_ITEMS.filter(({ to }) => isMenuItemVisible(to, user?.role));

  return (
    <aside className={["side", expanded ? "" : "is-collapsed"].filter(Boolean).join(" ")}>
      <button
        type="button"
        className="icon-btn side-toggle"
        onClick={() => setExpanded((prev) => !prev)}
        aria-label={expanded ? "Collapse sidebar" : "Expand sidebar"}
      >
        {expanded ? <ChevronLeft size={18} /> : <ChevronRight size={18} />}
      </button>

      <nav className="side-nav" aria-label="Storage">
        {visibleItems.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} title={label} className="side-link">
            <Icon size={18} aria-hidden="true" />
            <span className="side-label">{label}</span>
          </NavLink>
        ))}
      </nav>

      {expanded ? (
        <div className="side-foot">
          <StorageUsageBar />
        </div>
      ) : null}
    </aside>
  );
}
