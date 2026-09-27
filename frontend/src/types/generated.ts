export interface GeneratedListItem {
  id: string;
  created_at: string;
  prompt: string;
}

export interface GeneratedListResponse {
  items: GeneratedListItem[];
}

export interface GeneratedRun {
  id: string;
  created_at: string;
  prompt: string;
  negative_prompt: string;
  seed: number;
  steps: number;
  true_cfg_scale: number;
  width: number;
  height: number;
  duration: number;
  references: string[];
  result: string;
}
