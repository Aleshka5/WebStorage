import { useEffect, useState, type ComponentType } from "react";
import { NavLink } from "react-router-dom";
import {
  Camera,
  ChevronLeft,
  ChevronRight,
  Folder,
  Lock,
  Settings,
  Users,
} from "lucide-react";
import { useAuthStore } from "../../store/auth";
import { StorageUsageBar } from "./StorageUsageBar";

const STORAGE_KEY = "homecloud-sidebar-expanded";

interface MenuItem {
  to: string;
  label: string;
  icon: ComponentType<{ className?: string; size?: number | string }>;
}

const MENU_ITEMS: MenuItem[] = [
  { to: "/photos", label: "Фото", icon: Camera },
  { to: "/files", label: "Файлы", icon: Folder },
  { to: "/private", label: "Приватное", icon: Lock },
  { to: "/shared", label: "Общее", icon: Users },
  { to: "/admin", label: "Админка", icon: Settings },
];

function isMenuItemVisible(to: string, role: string | undefined): boolean {
  if (to === "/shared") {
    return role === "FAMILY" || role === "ADMIN";
  }

  if (to === "/admin") {
    return role === "ADMIN";
  }

  return true;
}

function readExpandedState(): boolean {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === null) return true;
    return stored === "true";
  } catch {
    return true;
  }
}

export function Sidebar() {
  const user = useAuthStore((state) => state.user);
  const [expanded, setExpanded] = useState(readExpandedState);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(expanded));
    } catch {
      // ignore write errors
    }
  }, [expanded]);

  const toggle = () => setExpanded((prev) => !prev);
  const visibleItems = MENU_ITEMS.filter(({ to }) => isMenuItemVisible(to, user?.role));

  return (
    <aside
      className={[
        "flex shrink-0 flex-col border-r border-zinc-800 bg-zinc-900 transition-[width] duration-300 ease-in-out",
        expanded ? "w-60" : "w-16",
      ].join(" ")}
    >
      <div className="flex items-center justify-end p-2">
        <button
          type="button"
          onClick={toggle}
          aria-label={expanded ? "Свернуть боковую панель" : "Развернуть боковую панель"}
          className="flex h-8 w-8 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100"
        >
          {expanded ? <ChevronLeft size={18} /> : <ChevronRight size={18} />}
        </button>
      </div>

      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto px-2 pb-2">
        {visibleItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            title={expanded ? undefined : label}
            className={({ isActive }) =>
              [
                "group relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                isActive
                  ? "bg-sky-600/20 text-sky-400"
                  : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100",
                expanded ? "" : "justify-center px-0",
              ].join(" ")
            }
          >
            <Icon size={20} className="shrink-0" />
            <span
              className={[
                "whitespace-nowrap transition-opacity duration-300",
                expanded ? "opacity-100" : "pointer-events-none w-0 overflow-hidden opacity-0",
              ].join(" ")}
            >
              {label}
            </span>

            {!expanded && (
              <span
                role="tooltip"
                className="pointer-events-none absolute left-full z-50 ml-2 hidden whitespace-nowrap rounded-md bg-zinc-800 px-2 py-1 text-xs text-zinc-100 shadow-lg group-hover:block"
              >
                {label}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      {expanded && <StorageUsageBar />}
    </aside>
  );
}
