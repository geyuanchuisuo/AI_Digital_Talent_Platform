/**
 * 画像详情页（T-21）。路由 `/talents/[id]`；数据全部客户端获取（token 在 localStorage，
 * 与守卫/请求层一致）。经历类只读展示（D-J），编辑入口只针对画像自有字段。
 */

'use client';

import { useParams } from 'next/navigation';

import DetailView from './DetailView';

export default function TalentDetailPage() {
  const params = useParams<{ id: string }>();
  const talentId = Number(params?.id);
  if (!Number.isInteger(talentId) || talentId <= 0) {
    return <div style={{ padding: 40, textAlign: 'center' }}>无效的画像编号</div>;
  }
  return <DetailView talentId={talentId} />;
}
