/**
 * 岗位条件页业务类型（T-26）。口径：接口 §6 + `backend/app/schemas/job_profile.py` 逐字对齐。
 */

/** 学历六档（决策 D-G 字面值） */
export const DEGREE_OPTIONS = ['博士', '硕士', '本科', '大专', '高中中专', '其他'] as const;

export interface HardRuleSwitches {
  degree: boolean;
  work_years: boolean;
  must_skills: boolean;
}

export interface Weights {
  skills: number;
  experience: number;
  education: number;
  others: number;
}

/** `GET /job-profiles/{id}` / POST / PUT 响应（完整配置） */
export interface JobProfileOut {
  id: number;
  title: string;
  department: string | null;
  jd_text: string;
  requirements_text: string;
  min_degree: string | null;
  min_work_years: number | null;
  must_skills: string[];
  bonus_skills: string[];
  hard_rule_switches: HardRuleSwitches;
  pass_threshold: number;
  pending_threshold: number;
  weights: Weights;
  version: number;
  is_active: boolean;
  created_by: number | null;
  updated_by: number | null;
  created_at: string;
  updated_at: string | null;
}

/** `GET /job-profiles` 的 items 项（不含全文） */
export interface JobProfileListItem {
  id: number;
  title: string;
  department: string | null;
  min_degree: string | null;
  min_work_years: number | null;
  must_skills: string[];
  bonus_skills: string[];
  version: number;
  is_active: boolean;
  updated_at: string | null;
}

/** POST / PUT 请求体（写入字段；PUT 不带 id/created_at 等响应字段） */
export interface JobProfileWritePayload {
  title: string;
  department?: string | null;
  jd_text: string;
  requirements_text: string;
  min_degree?: string | null;
  min_work_years?: number | null;
  must_skills?: string[];
  bonus_skills?: string[];
  hard_rule_switches?: HardRuleSwitches;
  pass_threshold?: number;
  pending_threshold?: number;
  weights: Weights;
}

export interface JobProfileDeleteData {
  deleted: boolean;
  soft_deleted: boolean;
}

/** POST /job-profiles/jd-draft 请求体（T-A22，素材均可空除 title） */
export interface JdDraftPayload {
  title: string;
  department?: string | null;
  requirements_text?: string | null;
  min_degree?: string | null;
  must_skills?: string[];
  bonus_skills?: string[];
  remarks?: string | null;
}

/** POST /job-profiles/jd-draft 响应 data：草稿 markdown + 版本留痕 */
export interface JdDraftData {
  draft_md: string;
  model_version: string;
  prompt_version: string;
  generated_at: string;
}
