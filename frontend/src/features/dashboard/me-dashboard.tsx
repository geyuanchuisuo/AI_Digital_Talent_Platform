'use client';

/**
 * 个人工作台卡片（T-D23）：本人待测评 / 培训 / 已公布近期结果三要素。
 *
 * 设计为可嵌入组件：由 (person)/home（A 归属）挂载替换占位卡；
 * 组件自身只负责展示三要素计数 + 口径说明，不引入路由跳转（个人端首页入口由 A 提供）。
 * 权限由后端强制：hr 403、未绑定 person 账号的 personal 行也仅返回本人行数。
 */

import { useCallback, useEffect, useState } from 'react';

import { fetchMeOverview, type MeOverview } from './me-api';
import { getErrorMessage } from '@/lib/http';
import { Card, Col, Row, Statistic, Typography, message } from 'antd';

const { Text } = Typography;

export default function MeDashboardCard() {
  const [data, setData] = useState<MeOverview | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fetchMeOverview());
    } catch (e) {
      message.error(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const items: Array<{ key: keyof MeOverview; title: string }> = [
    { key: 'pending_assignments', title: '待测评' },
    { key: 'pending_training', title: '培训计划' },
    { key: 'recent_results', title: '已公布结果' },
  ];

  return (
    <Card title="我的工作台" style={{ height: '100%' }}>
      <Row gutter={[8, 12]}>
        {items.map((item) => (
          <Col xs={24} sm={8} key={item.key}>
            <Statistic title={item.title} value={data?.[item.key] ?? '-'} loading={loading} />
          </Col>
        ))}
      </Row>
      <Text type="secondary" style={{ display: 'block', marginTop: 12, fontSize: 12 }}>
        {data?.caliber?.pending_assignments && data.caliber.pending_assignments}
        {data?.caliber?.pending_training && data.caliber.pending_training}
        {data?.caliber?.recent_results && data.caliber.recent_results}
      </Text>
    </Card>
  );
}
