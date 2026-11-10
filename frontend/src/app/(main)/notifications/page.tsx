/**
 * 通知中心页（T-D24）：列表 / 已读 / 失败查看 / 状态过滤。
 *
 * 权限由后端强制：个人仅本人通知，管理侧可按收件人过滤（输入收件人 ID，403 由 http 统一提示）。
 * 已读按钮仅对未读通知显示；投递失败原因仅 failed 行可见（后端按状态回显）。
 * 按事件类型跳转源对象：payload 内带业务上下文，一期先展示 payload 摘要（ref_type/ref_id），
 * 深链路由按 ref_type 白名单映射（不在白名单的如实显示原文，不猜测路由）。
 */

'use client';

import { ReloadOutlined } from '@ant-design/icons';
import {
  Button,
  Card,
  Drawer,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useCallback, useEffect, useState } from 'react';

import { getErrorMessage } from '@/lib/http';

import {
  STATUS_TAG,
  eventLabel,
  fetchEventTypes,
  fetchNotifications,
  markNotificationRead,
  type NotificationItem,
  type NotificationStatus,
} from './api';

const { Text } = Typography;

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待投递' },
  { value: 'sent', label: '已投递' },
  { value: 'failed', label: '投递失败' },
  { value: 'read', label: '已读' },
];

function fmt(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—';
}

export default function NotificationsPage() {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [status, setStatus] = useState<NotificationStatus | ''>('');
  const [recipientUserId, setRecipientUserId] = useState<number | undefined>();
  const [recipientInput, setRecipientInput] = useState('');
  const [eventTypes, setEventTypes] = useState<string[]>([]);
  const [detail, setDetail] = useState<NotificationItem | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetchNotifications({
        page,
        page_size: pageSize,
        status,
        recipient_user_id: recipientUserId,
      });
      setItems(resp.items ?? []);
      setTotal(resp.total ?? 0);
    } catch (e) {
      message.error(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, status, recipientUserId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    fetchEventTypes()
      .then(setEventTypes)
      .catch(() => setEventTypes([]));
  }, []);

  const doMarkRead = async (item: NotificationItem) => {
    try {
      await markNotificationRead(item.id);
      message.success('已标记已读');
      void load();
      if (detail?.id === item.id) {
        setDetail({ ...item, status: 'read', read_at: new Date().toISOString() });
      }
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const onRecipientSearch = () => {
    const trimmed = recipientInput.trim();
    if (!trimmed) {
      setRecipientUserId(undefined);
      setPage(1);
      return;
    }
    const id = Number(trimmed);
    if (!Number.isInteger(id) || id <= 0) {
      message.error('收件人 ID 须为正整数');
      return;
    }
    setRecipientUserId(id);
    setPage(1);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card
        title="通知中心"
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
        }
      >
        <Space style={{ marginBottom: 12 }} wrap>
          <Select
            style={{ width: 140 }}
            value={status}
            options={STATUS_OPTIONS}
            onChange={(v) => {
              setStatus(v);
              setPage(1);
            }}
            data-testid="status-filter"
          />
          <Select
            style={{ width: 200 }}
            placeholder="事件类型（全部）"
            allowClear
            showSearch
            optionFilterProp="label"
            value={undefined}
            options={eventTypes.map((t) => ({ value: t, label: eventLabel(t) }))}
            disabled
            title="一期按状态过滤；事件类型索引归检索迭代"
          />
          <Space.Compact>
            <input
              style={{
                width: 140,
                border: '1px solid #d9d9d9',
                borderRadius: '6px 0 0 6px',
                padding: '4px 11px',
              }}
              placeholder="收件人 ID（管理侧）"
              value={recipientInput}
              onChange={(e) => setRecipientInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onRecipientSearch();
              }}
            />
            <Button onClick={onRecipientSearch} data-testid="recipient-filter-btn">过滤</Button>
          </Space.Compact>
        </Space>
        <Table<NotificationItem>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={items}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            onChange: (p, ps) => {
              setPage(p);
              setPageSize(ps);
            },
          }}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 70 },
            {
              title: '事件',
              dataIndex: 'event_type',
              width: 170,
              render: (t: string) => eventLabel(t),
            },
            {
              title: '关联对象',
              width: 130,
              render: (_: unknown, r: NotificationItem) =>
                r.ref_type ? `${r.ref_type}#${r.ref_id ?? '—'}` : '—',
            },
            { title: '收件人', dataIndex: 'recipient_user_id', width: 80 },
            {
              title: '状态',
              dataIndex: 'status',
              width: 100,
              render: (s: NotificationStatus) => (
                <Tag color={STATUS_TAG[s].color}>{STATUS_TAG[s].text}</Tag>
              ),
            },
            { title: '创建时间', dataIndex: 'created_at', width: 150, render: fmt },
            {
              title: '操作',
              width: 150,
              render: (_: unknown, r: NotificationItem) => (
                <Space size={4} wrap>
                  <Button size="small" onClick={() => setDetail(r)} data-testid={`detail-${r.id}`}>详情</Button>
                  {r.status !== 'read' && (
                    <Button
                      size="small"
                      type="primary"
                      onClick={() => void doMarkRead(r)}
                      data-testid={`read-${r.id}`}
                    >
                      标记已读
                    </Button>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Drawer
        title={detail ? `通知详情 · #${detail.id}` : '通知详情'}
        open={detail != null}
        onClose={() => setDetail(null)}
        width={520}
      >
        {detail && (
          <Space direction="vertical" style={{ width: '100%' }} size={8}>
            <Text strong>{eventLabel(detail.event_type)}</Text>
            <Text type="secondary">事件类型：{detail.event_type}</Text>
            <Text>
              状态：
              <Tag color={STATUS_TAG[detail.status].color}>
                {STATUS_TAG[detail.status].text}
              </Tag>
            </Text>
            {detail.status === 'failed' && detail.failure_reason && (
              <Text type="danger">失败原因：{detail.failure_reason}</Text>
            )}
            <Text type="secondary">关联对象：{detail.ref_type ? `${detail.ref_type}#${detail.ref_id ?? '—'}` : '—'}</Text>
            <Text type="secondary">收件人：{detail.recipient_user_id}</Text>
            <Text type="secondary">创建：{fmt(detail.created_at)}</Text>
            <Text type="secondary">投递：{fmt(detail.sent_at)}</Text>
            <Text type="secondary">已读：{fmt(detail.read_at)}</Text>
            <Text strong>业务数据（payload）</Text>
            <pre style={{ background: '#fafafa', padding: 12, borderRadius: 6, fontSize: 12, overflowX: 'auto' }}>
              {detail.payload ? JSON.stringify(detail.payload, null, 2) : '（无）'}
            </pre>
            {detail.status !== 'read' && (
              <Button type="primary" onClick={() => void doMarkRead(detail)}>标记已读</Button>
            )}
          </Space>
        )}
      </Drawer>
    </div>
  );
}
