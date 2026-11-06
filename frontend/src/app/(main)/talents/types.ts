/**
 * 人才画像页业务类型（T-20）。
 *
 * 口径：`docs/接口设计.md` v1.3 §5 + `backend/app/schemas/talent.py`（T-17/T-18/T-19 已落地）。
 * 字段名与后端逐字一致，不做重命名以免前端再维护一份映射表。
 */

/** 画像向量状态三态（数据库 §5.1） */
export type VectorStatus = 'pending' | 'synced' | 'failed';

/** `GET /talents` 的 items 项 */
export interface TalentListItem {
  id: number;
  name: string;
  current_title: string | null;
  work_years: number | null;
  degree: string | null;
  city: string | null;
  /** 前 8 个标签（后端截断） */
  tags: string[];
  /** 标签全量计数 */
  tag_total: number;
  source: string;
  manual_edited_fields: string[];
  vector_status: VectorStatus;
  /** 回收站视角据此显示删除时间；默认视角恒为 null */
  deleted_at: string | null;
  updated_at: string | null;
}

/** `GET /talents/search` 的 items 项（接口 §5：画像摘要 + similarity） */
export interface TalentSearchItem {
  id: number;
  name: string;
  current_title: string | null;
  work_years: number | null;
  degree: string | null;
  city: string | null;
  tags: string[];
  tag_total: number;
  vector_status: VectorStatus;
  /** COSINE 相似度 0–1，越大越相似 */
  similarity: number;
}

/** 标签类型四值（数据库 §5.2） */
export type TagType = 'skill' | 'industry' | 'level' | 'general';

/** 学历六档字面值见 `utils.ts` 的 DEGREE_OPTIONS（决策 D-G） */

/** `GET /talents/duplicates` 的 items 项（matched_value / phone 均为后端脱敏值） */
export interface DuplicateItem {
  id: number;
  reason: 'phone_match' | 'email_match' | 'name_phone_match';
  matched_value: string | null;
  status: 'pending' | 'merged' | 'kept';
  profile_a: { id: number; name: string; current_title: string | null; created_at: string } | null;
  profile_b: { id: number; name: string; current_title: string | null; created_at: string } | null;
  parsed_resume: { id: number; filename: string | null; name: string | null; phone: string | null } | null;
  created_at: string;
}

/** `POST /talents/duplicates/{id}/resolve` merge（200）响应 data */
export interface MergeResolveData {
  duplicate_id: number;
  status: 'merged';
  master_profile_id: number;
  vector_status: string;
  task_id: number | null;
  task_status: string | null;
}

/** `POST /talents/duplicates/{id}/resolve` keep（202）响应 data */
export interface KeepResolveData {
  duplicate_id: number;
  status: 'kept';
  talent_profile_id: number;
  task_id: number | null;
  task_status: string | null;
  stage: string | null;
}

/** `POST /talents/{id}/restore` 响应 data */
export interface RestoreTalentData {
  restored: boolean;
  vector_status: VectorStatus;
}

/** 详情页标签行（带类型与来源，接口 §5 GET /talents/{id}） */
export interface TagRow {
  tag: string;
  tag_type: TagType | string;
  source: 'llm' | 'manual' | string;
}

/** `GET /talents/{id}` 的 profile 对象（手机号/邮箱完整返回） */
export interface TalentProfileOut {
  id: number;
  name: string;
  gender: string | null;
  birth_year: number | null;
  phone: string | null;
  email: string | null;
  city: string | null;
  degree: string | null;
  work_years: number | null;
  current_title: string | null;
  expected_title: string | null;
  skills: string[];
  tags: TagRow[];
  ability_scores: Record<string, { score: number; basis: string }> | null;
  manual_edited_fields: string[];
  vector_status: VectorStatus;
  created_at: string;
  updated_at: string | null;
}

/** `GET /talents/{id}` 响应 data（经历类为聚合只读视图，D-J） */
export interface TalentDetailData {
  profile: TalentProfileOut;
  educations: Record<string, string | null>[];
  employments: Record<string, string | null>[];
  projects: Record<string, string | null>[];
  certificates: Record<string, string | null>[];
  resume_files: { resume_file_id: number; filename: string; upload_batch_id: string }[];
  screen_history: {
    batch_id: number;
    job_title: string | null;
    effective_verdict: string;
    score: number | null;
    created_at: string;
  }[];
  match_history: {
    batch_id: number;
    job_title: string | null;
    score: number;
    hard_fail: boolean;
    created_at: string;
  }[];
}

/** `PATCH /talents/{id}` 请求体（只接受画像自有字段；经历类键出现即 400） */
export interface TalentPatchPayload {
  name?: string;
  phone?: string | null;
  email?: string | null;
  city?: string | null;
  degree?: string | null;
  work_years?: number | null;
  current_title?: string | null;
  expected_title?: string | null;
  skills?: string[];
  /** 字符串或 {tag, tag_type} 对象混合 */
  tags?: (string | { tag: string; tag_type?: string })[];
  ability_scores?: Record<string, { score: number; basis: string }>;
}

/** `PATCH /talents/{id}` 响应 data */
export interface PatchTalentData {
  profile: TalentProfileOut;
  manual_edited_fields: string[];
  vector_status: VectorStatus;
  profile_text_changed: boolean;
}

/** `POST /talents/{id}/revector` 响应 data */
export interface RevectorTalentData {
  vector_status: VectorStatus;
  vector_synced_at: string | null;
}
