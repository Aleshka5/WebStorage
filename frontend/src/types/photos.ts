export interface PhotoItem {
  id: string;
  preview_url: string;
  original_url: string;
  created_at: string;
  size: number;
}

export interface PhotoListResponse {
  items: PhotoItem[];
  total: number;
  has_next: boolean;
}

export const PHOTO_BATCH_SIZE = 30;
