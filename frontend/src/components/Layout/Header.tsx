import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { UserCircle } from "lucide-react";
import { useAuthStore } from "../../store/auth";

const MOCK_USED_MB = 45;
const MOCK_LIMIT_MB = 100;

export function Header() {
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const usedPercent = Math.min(100, Math.round((MOCK_USED_MB / MOCK_LIMIT_MB) * 100));
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

  const handleLogout = async () => {
    setMenuOpen(false);
    await logout();
    navigate("/auth", { replace: true });
  };

  return (
    <header className="flex h-14 shrink-0 items-center border-b border-zinc-800 bg-zinc-900 px-6">
      <div className="flex w-40 shrink-0 items-center">
        <span className="text-lg font-semibold tracking-tight text-zinc-100">HomeCloud</span>
      </div>

      <div className="flex flex-1 flex-col items-center gap-1 px-4">
        <div className="h-2 w-full max-w-md overflow-hidden rounded-full bg-zinc-800">
          <div
            className="h-full rounded-full bg-sky-500 transition-all duration-300"
            style={{ width: `${usedPercent}%` }}
          />
        </div>
        <span className="text-xs text-zinc-400">
          {MOCK_USED_MB} МБ из {MOCK_LIMIT_MB} МБ
        </span>
      </div>

      <div className="relative flex w-40 shrink-0 justify-end" ref={menuRef}>
        <button
          type="button"
          onClick={() => setMenuOpen((prev) => !prev)}
          aria-label="Меню пользователя"
          aria-expanded={menuOpen}
          className="flex h-9 w-9 items-center justify-center rounded-full text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100"
        >
          <UserCircle size={28} />
        </button>

        {menuOpen && (
          <div className="absolute right-0 top-full z-50 mt-2 w-56 overflow-hidden rounded-lg border border-zinc-700 bg-zinc-800 shadow-xl">
            <div className="px-4 py-3 text-sm text-zinc-300">{displayEmail}</div>
            <div className="border-t border-zinc-700" />
            <button
              type="button"
              onClick={() => void handleLogout()}
              className="w-full px-4 py-2.5 text-left text-sm text-zinc-300 transition-colors hover:bg-zinc-700 hover:text-zinc-100"
            >
              Выйти
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
