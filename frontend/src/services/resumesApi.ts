import api from "./api";
import type {
  ResumeNode,
  ResumeStatusesResponse,
  ResumeStatusInput,
  ResumeTreeResponse,
  VacancyListResponse,
  VacancyMeta,
  VacancyMetaUpdate,
} from "../types/resumes";

export async function listResumeTree(path: string): Promise<ResumeTreeResponse> {
  const { data } = await api.get<ResumeTreeResponse>("/api/resumes/tree", {
    params: { path },
  });
  return data;
}

export async function createResumeNode(
  path: string,
  name: string,
  statusId?: string | null,
): Promise<ResumeNode> {
  const payload: { path: string; name: string; status_id?: string | null } = {
    path,
    name,
  };

  if (statusId !== undefined) {
    payload.status_id = statusId;
  }

  const { data } = await api.post<ResumeNode>("/api/resumes/tree", payload);
  return data;
}

export async function renameResumeNode(path: string, newName: string): Promise<ResumeNode> {
  const { data } = await api.patch<ResumeNode>("/api/resumes/tree", {
    path,
    new_name: newName,
  });
  return data;
}

export async function deleteResumeNode(path: string): Promise<void> {
  await api.delete("/api/resumes/tree", { params: { path } });
}

export async function listAllVacancies(): Promise<VacancyListResponse> {
  const { data } = await api.get<VacancyListResponse>("/api/resumes/vacancies");
  return data;
}

export async function getVacancyMeta(path: string): Promise<VacancyMeta> {
  const { data } = await api.get<VacancyMeta>("/api/resumes/vacancy", {
    params: { path },
  });
  return data;
}

export async function saveVacancyMeta(
  path: string,
  meta: VacancyMetaUpdate,
): Promise<VacancyMeta> {
  const { data } = await api.put<VacancyMeta>("/api/resumes/vacancy", meta, {
    params: { path },
  });
  return data;
}

export async function listResumeStatuses(): Promise<ResumeStatusesResponse> {
  const { data } = await api.get<ResumeStatusesResponse>("/api/resumes/statuses");
  return data;
}

export async function saveResumeStatuses(
  statuses: ResumeStatusInput[],
): Promise<ResumeStatusesResponse> {
  const { data } = await api.put<ResumeStatusesResponse>("/api/resumes/statuses", {
    statuses,
  });
  return data;
}
