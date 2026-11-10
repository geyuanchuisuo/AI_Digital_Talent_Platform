/**
 * 管理员工作台页（T-D22）：六类真实数量卡片 + 业务入口。
 *
 * 权限由后端强制：personal 账号 403（deny-by-default 门禁）。
 * 六类数字实时查询（不缓存），新增业务数据后刷新即变（验收③）；
 * 卡片附口径说明，数字可回查对应业务记录（验收①）；
 * 响应只含计数，不含未公布成绩/内部反馈/人才池信息（验收②）。
 */

'use client';

import { ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Col, Row, Statistic, Tag, Typography, message } from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';

import { getErrorMessage } from '@/lib/http';

import { CARD_ORDER, fetchAdminOverview, type AdminOverview, type CardInfo } from './api';

const { Text } = Typography;

const CARD_COLORS: Record<string, string> = {
  jobs: 'blue',
  profiles: 'geekblue',
  reviews: 'orange',
  scoring: 'gold',
  training: 'cyan',
  gaps: 'red',
};

export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<AdminOverview | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fetchAdminOverview());
    } catch (e) {
      message.error(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Card
      title="管理员工作台"
      extra={
        <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
          刷新
        </Button>
      }
    >
      <Alert
        type="info"
        showIcon
        message="六类数量经各域授权结果服务实时读取；统计只含计数，不含未公布成绩、内部面试反馈与人才池信息。"
        style={{ marginBottom: 16 }}
      />
      <Row gutter={[16, 16]}>
        {CARD_ORDER.map((key) => {
          const card: CardInfo | undefined = data?.cards?.[key];
          return (
            <Col key={key} xs={24} sm={12} lg={8}>
              <Card
                size="small"
                hoverable
                onClick={() => card && router.push(card.entry)}
                styles={{ body: { padding: '16px 24px' } }}
              >
                <Statistic
                  title={
                    <span>
                      <Tag color={CARD_COLORS[key]}>{card?.label ?? key}</Tag>
                    </span>
                  }
                  value={card?.count ?? '-'}
                  loading={loading}
                />
                <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
                  {card?.caliber ?? ''}
                </Text>
              </Card>
            </Col>
          );
        })}
      </Row>
      {data?.cards?.reviews?.breakdown && (
        <Card size="small" title="待复核分项" style={{ marginTop: 16, maxWidth: 640 }}>
          <SpaceBreakdown breakdown={data.cards.reviews.breakdown as Record<string, number>} />
        </Card>
      )}
      {data?.cards?.gaps?.breakdown && (
        <Card size="small" title="缺口预警分项" style={{ marginTop: 16, maxWidth: 640 }}>
          <SpaceBreakdown breakdown={data.cards.gaps.breakdown as Record<string, number>} />
          <Text type="secondary" style={{ fontSize: 12 }}>
            明细回查：问数页指标 job.demand_progress_gap
          </Text>
        </Card>
      )}
      {data && (
        <Text type="secondary" style={{ display: 'block', marginTop: 16, fontSize: 12 }}>
          数据源：{data.data_source === 'replica' ? '只读副本' : '主库'}（实时查询）
        </Text>
      )}
    </Card>
  );
}

function SpaceBreakdown({ breakdown }: { breakdown: Record<string, number> }) {
  return (
    <Row gutter={16}>
      {Object.entries(breakdown)
        .filter(([key]) => typeof breakdown[key] === 'number')
        .map(([key, value]) => (
          <Col key={key} span={8}>
            <Statistic title={key} value={value} valueStyle={{ fontSize: 18 }} />
          </Col>
        ))}
    </Row>
  );
}
