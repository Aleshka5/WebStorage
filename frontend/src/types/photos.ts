export interface PhotoItem {
  id: string;
  preview_url: string;
  original_url: string;
  created_at?: string;
  size?: number;
}

export interface PhotoListResponse {
  items: PhotoItem[];
  total: number;
  has_next: boolean;
}

export interface PhotoSearchHit {
  id: string;
  file_name: string;
  preview_url: string;
  original_url: string;
  score: number;
}

export interface PhotoSearchResponse {
  items: PhotoSearchHit[];
  query: string;
  total: number;
}

export const PHOTO_BATCH_SIZE = 30;
export const PHOTO_SEARCH_LIMIT = 20;
export const PHOTO_SEARCH_QUERY_MAX_LENGTH = 4000;
