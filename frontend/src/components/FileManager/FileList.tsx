import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import type { FileNode, SortDirection, SortField } from "../../types/files";
import { LoadingBlock } from "../ui/LoadingBlock";
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
  onDownloadFolder: (item: FileNode) => Promise<void>;
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
    return <ArrowUpDown size={14} aria-hidden="true" />;
  }

  return sortDirection === "asc" ? (
    <ArrowUp size={14} aria-hidden="true" />
  ) : (
    <ArrowDown size={14} aria-hidden="true" />
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
    <th className={className}>
      <button type="button" onClick={() => onSortChange(field)}>
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
  onDownloadFolder,
  onRename,
  onDeleteRequest,
}: FileListProps) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th aria-label="Type" />
            <SortableHeader
              label="Name"
              field="name"
              sortField={sortField}
              sortDirection={sortDirection}
              onSortChange={onSortChange}
            />
            <SortableHeader
              label="Size"
              field="size"
              sortField={sortField}
              sortDirection={sortDirection}
              onSortChange={onSortChange}
              className="hide-sm"
            />
            <SortableHeader
              label="Modified"
              field="modified_at"
              sortField={sortField}
              sortDirection={sortDirection}
              onSortChange={onSortChange}
              className="hide-md"
            />
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {isLoading ? (
            <tr>
              <td colSpan={5}>
                <LoadingBlock active label="Loading files…" />
              </td>
            </tr>
          ) : items.length === 0 ? (
            <tr>
              <td colSpan={5}>
                <p className="empty">This folder is empty. Drag files here or click Upload.</p>
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
                onDownloadFolder={onDownloadFolder}
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
