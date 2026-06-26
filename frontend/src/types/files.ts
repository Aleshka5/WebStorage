export interface FileNode {
  name: string;
  is_dir: boolean;
  size: number;
  modified_at: string;
  path: string;
}

export type FileManagerMode = "plain" | "encrypted";

export type SortField = "name" | "size" | "modified_at";
export type SortDirection = "asc" | "desc";
