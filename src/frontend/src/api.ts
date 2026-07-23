export type Dataset = {
  id: string;
  dataset_key: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
};

export type CaseRevision = {
  id: string;
  case_id: string;
  revision_number: number;
  created_at: string;
};

export type DatasetCase = {
  id: string;
  dataset_id: string;
  case_key: string;
  category_id: string | null;
  source_filename: string | null;
  revisions: CaseRevision[];
};

export type CaseCategory = {
  id: string;
  dataset_id: string;
  category_key: string;
  name: string;
  description: string;
};

export type CaseRevisionContent = {
  case_id: string;
  case_key: string;
  revision_id: string;
  revision_number: number;
  reference_answer: string;
};

export type CaseRevisionPayload = {
  case_key?: string;
  reference_answer: string;
  category_key?: string;
  category_name?: string;
};

export type BenchmarkRun = {
  id: string;
  status: string;
  summary: Record<string, unknown>;
  manifest: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type BenchmarkCaseRun = {
  id: string;
  case_revision_id: string;
  status: string;
  stage: string;
  error_code?: string | null;
};

export type DirectResultListItem = {
  id: string;
  case_key: string;
  status: string;
  reports: { candidate_name: string; score: string; passed: boolean }[];
};

export type DirectResultClaim = {
  id: string;
  type: string;
  statement: string;
  importance: string;
  weight: number;
  relation: string;
  relation_label: string;
  overall_relation: string;
  conclusion_relation_label: string;
  score: string;
  evidence_keyword: string | null;
  keyword_match: boolean | null;
  keyword_score: string | null;
  conclusion_similarity: number | null;
  conclusion_score: string | null;
  closest_keyword_line: null | {
    line_number: number;
    quote: string;
    diagnostic_similarity: number;
  };
  candidate_quote: string | null;
};

export type DirectResultReportSummary = {
  candidate_name: string;
  status: string;
  score: string;
  passed: boolean;
  positive_score: string;
  penalties: string;
  claim_count: number;
  hit_count: number;
  missing_chains: string[];
  metrics: Record<string, unknown>;
  judge: { kind: string; runner: string };
  claims: DirectResultClaim[];
  warnings: unknown[];
};

export type DirectResultComparison = {
  baseline: string;
  candidate: string;
  delta: string;
  classification: string;
  classification_label: string;
};

export type DirectResultSummary = {
  case_key: string;
  case_source: Record<string, unknown>;
  engine_note: string;
  ranking: string[];
  reports: DirectResultReportSummary[];
  comparisons: DirectResultComparison[];
};

const apiBase = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const message = payload?.error?.message ?? payload?.detail ?? `请求失败 (${response.status})`;
    throw new ApiError(String(message), response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const analystBenchApi = {
  listDatasets: () => request<Dataset[]>("/datasets"),
  createDataset: (payload: { name: string; description: string; dataset_key?: string }) =>
    request<Dataset>("/datasets", { method: "POST", body: JSON.stringify(payload) }),
  deleteDataset: (datasetId: string) => request<void>(`/datasets/${datasetId}`, { method: "DELETE" }),
  listDatasetCases: (datasetId: string) => request<DatasetCase[]>(`/datasets/${datasetId}/cases`),
  listDatasetCategories: (datasetId: string) =>
    request<CaseCategory[]>(`/datasets/${datasetId}/categories`),
  createDatasetCategory: (datasetId: string, payload: { category_key: string; name?: string; description?: string }) =>
    request<CaseCategory>(`/datasets/${datasetId}/categories`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteDatasetCategory: (datasetId: string, categoryId: string) =>
    request<void>(`/datasets/${datasetId}/categories/${categoryId}`, { method: "DELETE" }),
  createCase: (datasetId: string, payload: CaseRevisionPayload) =>
    request<CaseRevision>(`/datasets/${datasetId}/cases`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteCase: (caseId: string) => request<void>(`/cases/${caseId}`, { method: "DELETE" }),
  createCaseRevision: (caseId: string, payload: CaseRevisionPayload) =>
    request<CaseRevision>(`/cases/${caseId}/revisions`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getCaseRevisionContent: (revisionId: string) =>
    request<CaseRevisionContent>(`/case-revisions/${revisionId}/content`),
  createBenchmarkRun: (payload: {
    dataset_version_id: string;
    candidate_version_id: string;
    scoring_policy_version_id: string;
  }) =>
    request<BenchmarkRun>("/benchmark-runs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getBenchmarkRun: (runId: string) => request<BenchmarkRun>(`/benchmark-runs/${runId}`),
  getBenchmarkCaseRuns: (runId: string) =>
    request<BenchmarkCaseRun[]>(`/benchmark-runs/${runId}/case-runs`),
  getBenchmarkCaseResult: (caseRunId: string) =>
    request<Record<string, unknown>>(`/benchmark-case-runs/${caseRunId}/result`),
  listDirectResults: () => request<DirectResultListItem[]>("/direct-results"),
  getDirectResult: (resultId: string) =>
    request<Record<string, unknown>>(`/direct-results/${resultId}`),
  deleteDirectResult: (resultId: string) =>
    request<void>(`/direct-results/${resultId}`, { method: "DELETE" }),
};
