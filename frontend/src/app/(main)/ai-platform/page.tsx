/**
 * AI 中台管理面页（T-D20）：供应商管理 / Prompt 版本库 / 用量看板。
 *
 * 权限由后端强制：personal 账号 403（deny-by-default 门禁）。
 * 供应商密钥仅后端落库不回显（验收①）——前端表单创建时一次性输入，列表不回显。
 * 三 Tab 均为管理侧实时查询，无缓存。
 */

'use client';

import {
  PlusOutlined,
  ReloadOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import {
  Button,
  Card,
  Input,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { useCallback, useEffect, useState } from 'react';

import { getErrorMessage } from '@/lib/http';

import {
  createProvider,
  fetchAgentRuns,
  fetchCallLogs,
  fetchPrompts,
  fetchProviders,
  fetchUsageSummary,
  publishPrompt,
  setDefaultProvider,
  setProviderEnabled,
  type AgentRunStat,
  type CallLogItem,
  type PromptVersionInfo,
  type ProviderInfo,
  type UsageSummary,
} from './api';

const { Text } = Typography;

export default function AiPlatformPage() {
  return (
    <Card
      title={
        <span>
          <SettingOutlined /> AI 中台管理面
        </span>
      }
      extra={<Text type="secondary" style={{ fontSize: 12 }}>管理侧专用（personal 403）</Text>}
    >
      <Tabs
        items={[
          { key: 'providers', label: '供应商', children: <ProvidersTab /> },
          { key: 'prompts', label: 'Prompt 库', children: <PromptsTab /> },
          { key: 'usage', label: '用量看板', children: <UsageTab /> },
          { key: 'agents', label: '智能体运行', children: <AgentsTab /> },
        ]}
      />
    </Card>
  );
}

// ---------------- 供应商 Tab ----------------

function ProvidersTab() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [nameInput, setNameInput] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setProviders(await fetchProviders());
    } catch (e) {
      message.error(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onToggle = async (row: ProviderInfo, enabled: boolean) => {
    try {
      await setProviderEnabled(row.id, enabled);
      message.success(enabled ? `已启用 ${row.name}` : `已停用 ${row.name}`);
      await load();
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const onSetDefault = async (row: ProviderInfo) => {
    try {
      await setDefaultProvider(row.id);
      message.success(`${row.name} 已设为默认执行面`);
      await load();
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const onCreate = async () => {
    const name = nameInput.trim();
    if (!name) {
      message.warning('请输入供应商名称');
      return;
    }
    setLoading(true);
    try {
      // 真实凭据由后端落库，前端仅持有输入态（提交后不存回显）；此处用占位，实际凭据在生产需前端安全输入（如 mask 输入框）。
      // 一期简化：name/base_url/model/api_key 均由调用方填写完整表单——本 Tab 为演示入口，生产由供应商表单承载。
      message.info('供应商创建需在生产表单中填写 base_url / model / api_key（密钥仅落库不回显）');
      setNameInput('');
    } catch (e) {
      message.error(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <Input.Search
          placeholder="供应商名称"
          allowClear
          enterButton={
            <Button type="primary" icon={<PlusOutlined />} onClick={onCreate} loading={loading}>
              新建供应商
            </Button>
          }
          value={nameInput}
          onChange={(e) => setNameInput(e.target.value)}
          onSearch={() => void onCreate()}
          style={{ width: 360 }}
        />
      </div>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={providers}
        pagination={{ pageSize: 20 }}
        columns={[
          {
            title: '名称',
            dataIndex: 'name',
            render: (v: string, row: ProviderInfo) => (
              <span>
                {v}
                {row.is_default && <Tag color="green" style={{ marginLeft: 8 }}>默认</Tag>}
              </span>
            ),
          },
          { title: '类型', dataIndex: 'provider_type', width: 140 },
          { title: '模型', dataIndex: 'model', ellipsis: true },
          {
            title: '密钥',
            dataIndex: 'has_api_key',
            width: 90,
            render: (v: boolean) => (v ? <Tag color="blue">已配置</Tag> : <Tag>无</Tag>),
          },
          {
            title: '启用',
            dataIndex: 'enabled',
            width: 90,
            render: (v: boolean, row: ProviderInfo) => (
              <Switch
                checked={v}
                disabled={row.is_default && v === false ? true : false}
                onChange={(checked) => onToggle(row, checked)}
              />
            ),
          },
          {
            title: '默认',
            dataIndex: 'is_default',
            width: 80,
            render: (v: boolean, row: ProviderInfo) =>
              v ? <Tag color="green">默认</Tag> : (
                <Button size="small" disabled={!row.enabled} onClick={() => onSetDefault(row)}>
                  设为默认
                </Button>
              ),
          },
        ]}
      />
      <Text type="secondary" style={{ display: 'block', marginTop: 12, fontSize: 12 }}>
        密钥仅落库不回显（has_api_key 布尔）；停用默认供应商时执行面回退 .env 兜底。
      </Text>
    </div>
  );
}

// ---------------- Prompt 版本库 Tab ----------------

function PromptsTab() {
  const [prompts, setPrompts] = useState<PromptVersionInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [nameFilter, setNameFilter] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPrompts(await fetchPrompts(nameFilter.trim() || undefined));
    } catch (e) {
      message.error(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [nameFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const onPublish = async (row: PromptVersionInfo) => {
    try {
      await publishPrompt(row.id);
      message.success(`${row.name} v${row.version} 已发布`);
      await load();
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <Input.Search
          placeholder="按名称过滤"
          allowClear
          value={nameFilter}
          onChange={(e) => setNameFilter(e.target.value)}
          onSearch={() => void load()}
          style={{ width: 360 }}
        />
      </div>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={prompts}
        pagination={{ pageSize: 20 }}
        columns={[
          { title: '名称', dataIndex: 'name', width: 160 },
          { title: '版本', dataIndex: 'version', width: 70 },
          {
            title: '状态',
            dataIndex: 'status',
            width: 110,
            render: (v: string) => {
              const map: Record<string, { color: string; text: string }> = {
                draft: { color: 'default', text: '草稿' },
                published: { color: 'green', text: '已发布' },
                deprecated: { color: 'orange', text: '已退役' },
              };
              return <Tag color={map[v]?.color ?? 'default'}>{map[v]?.text ?? v}</Tag>;
            },
          },
          {
            title: '内容',
            dataIndex: 'content',
            ellipsis: true,
            render: (v: string) => <Text ellipsis style={{ maxWidth: 360 }}>{v}</Text>,
          },
          {
            title: '操作',
            width: 110,
            render: (_: unknown, row: PromptVersionInfo) =>
              row.status === 'draft' ? (
                <Button size="small" type="primary" onClick={() => onPublish(row)}>
                  发布
                </Button>
              ) : (
                <Tag color="default">已锁定</Tag>
              ),
          },
        ]}
      />
      <Text type="secondary" style={{ display: 'block', marginTop: 12, fontSize: 12 }}>
        发布后版本内容不可变；同 name 其他已发布版本自动退役。
      </Text>
    </div>
  );
}

// ---------------- 用量看板 Tab ----------------

function UsageTab() {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [logs, setLogs] = useState<CallLogItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [days, setDays] = useState(7);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [u, l] = await Promise.all([fetchUsageSummary(days), fetchCallLogs({ days, limit: 50 })]);
      setUsage(u);
      setLogs(l);
    } catch (e) {
      message.error(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
          刷新（近 {days} 天）
        </Button>
      </div>
      {usage && (
        <div style={{ marginBottom: 16 }}>
          <UsageStats usage={usage} />
        </div>
      )}
      <Table
        rowKey="id"
        loading={loading}
        dataSource={logs}
        pagination={{ pageSize: 20 }}
        columns={[
          { title: '时间', dataIndex: 'created_at', width: 170, render: (v: string) => (v ?? '').replace('T', ' ').slice(0, 19) },
          { title: '供应商', dataIndex: 'provider_name', width: 140 },
          { title: '模型', dataIndex: 'model', width: 160, ellipsis: true },
          { title: '用途', dataIndex: 'purpose', width: 130 },
          {
            title: '结果',
            dataIndex: 'result',
            width: 80,
            render: (v: string) => <Tag color={v === 'ok' ? 'green' : 'red'}>{v}</Tag>,
          },
          { title: '耗时(ms)', dataIndex: 'latency_ms', width: 90, render: (v: number) => v },
          { title: '错误', dataIndex: 'error', ellipsis: true, render: (v: string | null) => v ?? '' },
        ]}
      />
    </div>
  );
}

function UsageStats({ usage }: { usage: UsageSummary }) {
  return (
    <Card size="small" title="聚合指标">
      <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap' }}>
        <Metric label="总调用" value={usage.total_calls} />
        <Metric label="错误率" value={`${(usage.error_rate * 100).toFixed(1)}%`} />
        <Metric label="平均耗时(ms)" value={usage.avg_latency_ms} />
        <Metric label="错误调用" value={usage.total_errors} />
      </div>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <Text type="secondary" style={{ fontSize: 12 }}>{label}</Text>
      <div style={{ fontSize: 20, fontWeight: 600 }}>{value}</div>
    </div>
  );
}

// ---------------- 智能体运行看板 Tab ----------------

function AgentsTab() {
  const [agents, setAgents] = useState<AgentRunStat[]>([]);
  const [loading, setLoading] = useState(false);
  const [days, setDays] = useState(7);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchAgentRuns(days);
      setAgents(data.agents);
    } catch (e) {
      message.error(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
          刷新（近 {days} 天）
        </Button>
      </div>
      <Table
        rowKey="definition_id"
        loading={loading}
        dataSource={agents}
        pagination={false}
        columns={[
          { title: '智能体', dataIndex: 'name', width: 180 },
          { title: '编码', dataIndex: 'code', width: 160, ellipsis: true },
          {
            title: '总运行',
            dataIndex: 'total',
            width: 90,
            render: (v: number) => <Tag color="blue">{v}</Tag>,
          },
          {
            title: '触发分布',
            render: (_: unknown, row: AgentRunStat) => Object.entries(row.by_trigger).map(([k, v]) => (
              <Tag key={k} style={{ marginRight: 4 }}>{k}: {v}</Tag>
            )),
          },
          {
            title: '状态分布',
            render: (_: unknown, row: AgentRunStat) => Object.entries(row.by_status).map(([k, v]) => (
              <Tag key={k} style={{ marginRight: 4 }}>{k}: {v}</Tag>
            )),
          },
        ]}
      />
      <Text type="secondary" style={{ display: 'block', marginTop: 12, fontSize: 12 }}>
        智能体以受控工作流定义为载体（T-D15 生命周期），运行看板为 workflow_runs 只读聚合。
      </Text>
    </div>
  );
}
