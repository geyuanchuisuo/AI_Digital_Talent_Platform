import { describe, expect, it } from 'vitest';

import {
  DEGREE_OPTIONS,
  duplicateReasonMeta,
  formatSimilarity,
  formatTime,
  formatYears,
  vectorStatusMeta,
} from './utils';

describe('talents utils', () => {
  it('学历六档与决策 D-G 字面值一致', () => {
    expect(DEGREE_OPTIONS).toEqual(['博士', '硕士', '本科', '大专', '高中中专', '其他']);
  });

  it('formatYears：null 显示占位，整数不带小数点', () => {
    expect(formatYears(null)).toBe('—');
    expect(formatYears(4)).toBe('4 年');
    expect(formatYears(4.5)).toBe('4.5 年');
  });

  it('formatSimilarity：0.83 → 83%', () => {
    expect(formatSimilarity(0.83)).toBe('83%');
    expect(formatSimilarity(0.6667)).toBe('67%');
  });

  it('vectorStatusMeta：未知状态回退原文案', () => {
    expect(vectorStatusMeta('failed').label).toBe('向量同步失败');
    expect(vectorStatusMeta('synced').color).toBe('success');
    expect(vectorStatusMeta('unknown').label).toBe('unknown');
  });

  it('duplicateReasonMeta：D-O 三路细分', () => {
    expect(duplicateReasonMeta('name_phone_match').label).toBe('姓名+手机号相同');
    expect(duplicateReasonMeta('phone_match').label).toBe('手机号相同');
    expect(duplicateReasonMeta('email_match').label).toBe('邮箱相同');
  });

  it('formatTime：空值占位', () => {
    expect(formatTime(null)).toBe('—');
    expect(formatTime('2026-11-06T10:20:30')).toContain('2026-11-06');
  });
});
