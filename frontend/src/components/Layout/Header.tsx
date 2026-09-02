import { useEffect, useRef, useState } from "react";
import { UserCircle } from "lucide-react";
import { useAuthStore } from "../../store/auth";

export function Header() {
  const user = useAuthStore((state) => state.user);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const displayEmail = user?.email ?? "user@example.com";

  useEffect(() => {
    if (!menuOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [menuOpen]);

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-800 bg-zinc-900 px-4 sm:px-6">
      <span className="text-lg font-semibold tracking-tight text-zinc-100">HomeCloud</span>

      <div className="relative" ref={menuRef}>
        <button
          type="button"
          onClick={() => setMenuOpen((prev) => !prev)}
          aria-label="User menu"
          aria-expanded={menuOpen}
          className="flex h-9 w-9 items-center justify-center rounded-full text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100"
        >
          <UserCircle size={28} />
        </button>

        {menuOpen && (
          <div className="absolute right-0 top-full z-50 mt-2 w-56 overflow-hidden rounded-lg border border-zinc-700 bg-zinc-800 shadow-xl">
            <div className="px-4 py-3 text-sm text-zinc-300">{displayEmail}</div>
          </div>
        )}
      </div>
    </header>
  );
}
