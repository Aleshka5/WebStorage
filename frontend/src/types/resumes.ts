export type ResumeNodeLevel = "COUNTRY" | "COMPANY" | "VACANCY";

export interface ResumeNode {
  name: string;
  path: string;
  level: ResumeNodeLevel | null;
  child_count: number;
  modified_at: string;
  status_id?: string | null;
  website_url?: string;
}

export interface ResumeTreeResponse {
  path: string;
  level: ResumeNodeLevel | null;
  items: ResumeNode[];
}

export interface ResumeField {
  name: string;
  value: string;
}

export interface VacancyMeta {
  path: string;
  name: string;
  website_url: string;
  status_id: string | null;
  fields: ResumeField[];
}

export interface VacancyMetaUpdate {
  website_url: string;
  status_id: string | null;
  fields: ResumeField[];
}

export interface ResumeStatus {
  id: string;
  name: string;
  color: string;
}

export interface ResumeStatusInput {
  id?: string;
  name: string;
  color: string;
}

export interface VacancyListItem {
  country: string;
  company: string;
  name: string;
  path: string;
  status_id: string | null;
  website_url: string;
  modified_at: string;
}

export interface VacancyListResponse {
  items: VacancyListItem[];
}

export interface ResumeStatusesResponse {
  statuses: ResumeStatus[];
}
