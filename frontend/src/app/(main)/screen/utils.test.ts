import { describe, expect, it } from 'vitest';

import {
  canDeleteStatus,
  contractStatusMeta,
  formatBatchId,
  formatTime,
  stageLabel,
  statusMeta,
  summaryCount,
} from './utils';
import type { QueueSummary } from './types';

const SUMMARY: QueueSummary = {
  pending: 2,
  running: 1,
  success: 5,
  failed: 3,
  duplicate_pending: 1,
  cancelled: 4,
  total: 16,
};

describe('screen utils', () => {
  it('六状态都有中文文案，未知状态原样透出', () => {
    expect(statusMeta('pending').label).toBe('待处理');
    expect(statusMeta('running').label).toBe('解析中');
    expect(statusMeta('success').label).toBe('成功');
    expect(statusMeta('failed').label).toBe('失败');
    expect(statusMeta('duplicate_pending').label).toBe('查重待确认');
    expect(statusMeta('cancelled').label).toBe('已取消');
    expect(statusMeta('unknown').label).toBe('unknown');
  });

  it('契约状态：优先 task_status，duplicate_pending 单独提示，旧 status 兜底', () => {
    expect(contractStatusMeta('waiting').label).toBe('待处理');
    expect(contractStatusMeta('running').label).toBe('解析中');
    expect(contractStatusMeta('completed').label).toBe('已完成');
    expect(contractStatusMeta('failed').label).toBe('失败');
    expect(contractStatusMeta('cancelled').label).toBe('已取消');

    // duplicate_pending = completed + 标记，仍显示查重待确认
    expect(contractStatusMeta('completed', true).label).toBe('查重待确认');
    expect(contractStatusMeta('completed', true).color).toBe('warning');

    // 无契约字段时退回旧 status
    expect(contractStatusMeta(null, false, 'duplicate_pending').label).toBe('查重待确认');
    expect(contractStatusMeta(undefined, false, 'success').label).toBe('成功');
    expect(contractStatusMeta(undefined, false, undefined).label).toBe('—');

    // 未知契约值原样透出，不吞字段
    expect(contractStatusMeta('other' as never).label).toBe('other');
  });

  it('阶段文案：extract/profile/vector，空值与未知值', () => {
    expect(stageLabel('extract')).toBe('提取文本');
    expect(stageLabel('profile')).toBe('生成画像');
    expect(stageLabel('vector')).toBe('同步向量');
    expect(stageLabel(null)).toBe('—');
    expect(stageLabel('other')).toBe('other');
  });

  it('移除按钮：running 与 cancelled 不显示，其余可软取消', () => {
    expect(canDeleteStatus('running')).toBe(false);
    expect(canDeleteStatus('cancelled')).toBe(false);
    expect(canDeleteStatus('pending')).toBe(true);
    expect(canDeleteStatus('failed')).toBe(true);
    expect(canDeleteStatus('success')).toBe(true);
    expect(canDeleteStatus('duplicate_pending')).toBe(true);
  });

  it('summary 计数：all 取 total，指定状态取对应字段', () => {
    expect(summaryCount(SUMMARY, 'all')).toBe(16);
    expect(summaryCount(SUMMARY, 'cancelled')).toBe(4);
    expect(summaryCount(undefined, 'all')).toBe(0);
    expect(summaryCount(undefined, 'failed')).toBe(0);
  });

  it('ULID 缩略：超长保留首尾，空值显示占位', () => {
    expect(formatBatchId('01JABCDEFGHJKMNPQRSTVWXYZA')).toBe('01JABCDE…XYZA');
    expect(formatBatchId('short')).toBe('short');
    expect(formatBatchId(null)).toBe('—');
  });

  it('时间格式化：合法 ISO 转本地字符串，空值与非法值显示占位', () => {
    expect(formatTime('2026-11-05T10:20:30')).toBe('2026-11-05 10:20:30');
    expect(formatTime(null)).toBe('—');
    expect(formatTime('not-a-date')).toBe('—');
  });
});
