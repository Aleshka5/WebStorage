import { FileManager } from "../components/FileManager/FileManager";

export default function FilesPage() {
  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <h2 className="text-xl font-semibold text-zinc-100">Файлы</h2>
      <div className="min-h-0 flex-1">
        <FileManager apiPrefix="/api/files" mode="plain" />
      </div>
    </div>
  );
}
