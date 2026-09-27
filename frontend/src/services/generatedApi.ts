import api from "./api";
import type { GeneratedListResponse, GeneratedRun } from "../types/generated";

interface GeneratedRunResponse {
  id?: string;
  created_at?: string;
  prompt: string;
  negative_prompt?: string;
  seed: number;
  steps: number;
  true_cfg_scale: number;
  width: number;
  height: number;
  duration: number;
  references?: string[];
  result: string;
}

const RUN_ID_TIMESTAMP = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/;

function createdAtFromRunId(id: string): string {
  const match = RUN_ID_TIMESTAMP.exec(id);

  if (!match) {
    return id;
  }

  const [, year, month, day, hour, minute, second] = match;
  return `${year}-${month}-${day}T${hour}:${minute}:${second}Z`;
}

function fileUrl(id: string, value: string): string {
  if (value.startsWith("/") || value.startsWith("http://") || value.startsWith("https://")) {
    return value;
  }

  return `/api/generated/${encodeURIComponent(id)}/files/${encodeURIComponent(value)}`;
}

export async function listGenerated(): Promise<GeneratedListResponse> {
  const { data } = await api.get<GeneratedListResponse>("/api/generated");
  return data;
}

export async function getGenerated(id: string): Promise<GeneratedRun> {
  const { data } = await api.get<GeneratedRunResponse>(
    `/api/generated/${encodeURIComponent(id)}`,
  );
  const runId = data.id ?? id;

  return {
    id: runId,
    created_at: data.created_at ?? createdAtFromRunId(runId),
    prompt: data.prompt,
    negative_prompt: data.negative_prompt ?? "",
    seed: data.seed,
    steps: data.steps,
    true_cfg_scale: data.true_cfg_scale,
    width: data.width,
    height: data.height,
    duration: data.duration,
    references: (data.references ?? []).map((reference) => fileUrl(runId, reference)),
    result: fileUrl(runId, data.result),
  };
}

export async function deleteGenerated(id: string): Promise<void> {
  await api.delete(`/api/generated/${encodeURIComponent(id)}`);
}
