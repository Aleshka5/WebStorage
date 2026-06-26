import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import type { FileNode, SortDirection, SortField } from "../../types/files";
import { FileItem } from "./FileItem";

interface FileListProps {
  items: FileNode[];
  isLoading: boolean;
  showUploader?: boolean;
  sortField: SortField;
  sortDirection: SortDirection;
  onSortChange: (field: SortField) => void;
  onOpenFolder: (path: string) => void;
  onDownload: (item: FileNode) => Promise<void>;
  onRename: (item: FileNode, newName: string) => Promise<void>;
  onDeleteRequest: (item: FileNode) => void;
}

function SortIcon({
  field,
  sortField,
  sortDirection,
}: {
  field: SortField;
  sortField: SortField;
  sortDirection: SortDirection;
}) {
  if (field !== sortField) {
    return <ArrowUpDown className="h-3.5 w-3.5 opacity-40" aria-hidden="true" />;
  }

  return sortDirection === "asc" ? (
    <ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
  ) : (
    <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
  );
}

function SortableHeader({
  label,
  field,
  sortField,
  sortDirection,
  onSortChange,
  className = "",
}: {
  label: string;
  field: SortField;
  sortField: SortField;
  sortDirection: SortDirection;
  onSortChange: (field: SortField) => void;
  className?: string;
}) {
  return (
    <th className={`px-3 py-3 text-left ${className}`.trim()}>
      <button
        type="button"
        onClick={() => onSortChange(field)}
        className="inline-flex items-center gap-1 text-xs font-medium uppercase tracking-wide text-zinc-500 transition-colors hover:text-zinc-300"
      >
        {label}
        <SortIcon field={field} sortField={sortField} sortDirection={sortDirection} />
      </button>
    </th>
  );
}

export function FileList({
  items,
  isLoading,
  showUploader = false,
  sortField,
  sortDirection,
  onSortChange,
  onOpenFolder,
  onDownload,
  onRename,
  onDeleteRequest,
}: FileListProps) {
  return (
    <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/50">
      <table className="min-w-full border-collapse">
        <thead>
          <tr className="border-b border-zinc-800 bg-zinc-900/80">
            <th className="w-10 px-3 py-3" aria-label="Тип" />
            <SortableHeader
              label="Имя"
              field="name"
              sortField={sortField}
              sortDirection={sortDirection}
              onSortChange={onSortChange}
            />
            <SortableHeader
              label="Размер"
              field="size"
              sortField={sortField}
              sortDirection={sortDirection}
              onSortChange={onSortChange}
              className="hidden sm:table-cell"
            />
            <SortableHeader
              label="Изменён"
              field="modified_at"
              sortField={sortField}
              sortDirection={sortDirection}
              onSortChange={onSortChange}
              className="hidden md:table-cell"
            />
            <th className="px-3 py-3 text-right text-xs font-medium uppercase tracking-wide text-zinc-500">
              Действия
            </th>
          </tr>
        </thead>
        <tbody>
          {isLoading ? (
            <tr>
              <td colSpan={5} className="px-3 py-10 text-center text-sm text-zinc-500">
                Загрузка...
              </td>
            </tr>
          ) : items.length === 0 ? (
            <tr>
              <td colSpan={5} className="px-3 py-10 text-center text-sm text-zinc-500">
                Папка пуста. Перетащите файлы сюда или нажмите «Загрузить».
              </td>
            </tr>
          ) : (
            items.map((item) => (
              <FileItem
                key={item.path}
                item={item}
                showUploader={showUploader}
                onOpenFolder={onOpenFolder}
                onDownload={onDownload}
                onRename={onRename}
                onDeleteRequest={onDeleteRequest}
              />
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
