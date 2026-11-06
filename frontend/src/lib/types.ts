/**
 * 与后端约定的通用类型（接口设计 v1.3 §1.1）。
 *
 * ⚠️ 这里**只放跨页面通用的信封类型**，不放业务实体类型。
 * 业务字段（talents / resumes / matches ...）等对应接口落地后，
 * 以 `GET /openapi.json` 为准生成或手写对齐，避免现在凭文档猜字段名造成漂移。
 */

export interface ApiResponse<T> {
  code: number;
  message: string;
  data: T;
  /** 仅错误响应携带（接口 §1.1） */
  request_id?: string;
}

/** 列表类 data：`{items,total,page,page_size}`（接口 §1.1） */
export interface PageData<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/** 长任务/批次的通用状态（2s 轮询的停止条件，架构 §5.7） */
export type TaskStatus = 'pending' | 'running' | 'done' | 'failed' | 'cancelled';
