const MOCK_USED_MB = 45;
const MOCK_LIMIT_MB = 100;

export function StorageUsageBar() {
  const usedPercent = Math.min(100, Math.round((MOCK_USED_MB / MOCK_LIMIT_MB) * 100));

  return (
    <div className="border-t border-zinc-800 px-4 py-3">
      <div className="mb-1.5 h-2 w-full overflow-hidden rounded-full bg-zinc-800">
        <div
          className="h-full rounded-full bg-sky-500 transition-all duration-300"
          style={{ width: `${usedPercent}%` }}
        />
      </div>
      <span className="text-xs text-zinc-400">
        {MOCK_USED_MB} МБ из {MOCK_LIMIT_MB} МБ
      </span>
    </div>
  );
}
