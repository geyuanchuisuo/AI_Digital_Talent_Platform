/**
 * 工作流配置页（T-D15 受控生命周期：定义/版本管理 + 提交/审核发布/停用/回滚）。
 *
 * 权限：全部端点管理侧门禁（后端 403 由 http 统一提示）；四眼原则由后端强制
 * （提交人审核 403，前端透传文案）。节点/连线一期 JSON 表单输入（服务端整图
 * 校验兜底），可视化画布编辑器归 T-D16（Dify 对接）。
 */

'use client';

import { LinkOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import { useCallback, useEffect, useState } from 'react';

import { getErrorMessage } from '@/lib/http';

import {
  createDefinition,
  createDraftVersion,
  deactivateVersion,
  exportDifyDsl,
  fetchDefinitions,
  fetchNodeTypes,
  fetchRunDetail,
  fetchRuns,
  fetchVersionDetail,
  fetchVersions,
  importDifyDsl,
  reconcileDifyDsl,
  approveRun,
  cancelRun,
  cancelRunningRun,
  retryRun,
  startRun,
  reviewVersion,
  rollbackDefinition,
  submitVersion,
  type DifyReconcileReport,
  type NodeTypeMeta,
  type VersionDetail,
  type WorkflowRunDetail,
  type WorkflowRunSummary,
  type WorkflowDefinition,
  type WorkflowVersion,
  type WorkflowVersionStatus,
} from './api';
import { GraphCanvas, type GraphValue } from '@/features/workflows/GraphCanvas';

const { Text } = Typography;

const STATUS_TAG: Record<WorkflowVersionStatus, { color: string; text: string }> = {
  draft: { color: 'default', text: '草稿' },
  pending_review: { color: 'gold', text: '待审核' },
  published: { color: 'green', text: '已发布' },
  deprecated: { color: 'red', text: '已停用' },
};

function fmt(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—';
}

export default function WorkflowsPage() {
  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [detail, setDetail] = useState<VersionDetail | null>(null);
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([]);
  const [runDetail, setRunDetail] = useState<WorkflowRunDetail | null>(null);
  const [runParams, setRunParams] = useState('{}');
  const [defModalOpen, setDefModalOpen] = useState(false);
  const [draftModalOpen, setDraftModalOpen] = useState(false);
  const [draftText, setDraftText] = useState('{\n  "nodes": [],\n  "edges": []\n}');
  const [rejectTarget, setRejectTarget] = useState<WorkflowVersion | null>(null);
  const [rejectNote, setRejectNote] = useState('');
  const [draftTab, setDraftTab] = useState('canvas');
  const [canvasValue, setCanvasValue] = useState<GraphValue>({ nodes: [], edges: [] });
  const [nodeTypes, setNodeTypes] = useState<NodeTypeMeta[]>([]);
  const [difyModalOpen, setDifyModalOpen] = useState(false);
  const [difyTab, setDifyTab] = useState('export');
  const [difyYaml, setDifyYaml] = useState('');
  const [difyExport, setDifyExport] = useState<{ app_name: string; node_count: number; edge_count: number; yaml: string } | null>(null);
  const [difyReport, setDifyReport] = useState<DifyReconcileReport | null>(null);
  const [difyBusy, setDifyBusy] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    if (!draftModalOpen) return;
    fetchNodeTypes()
      .then((meta) => setNodeTypes(meta.node_types))
      .catch((e) => message.error(getErrorMessage(e)));
  }, [draftModalOpen]);

  const loadDefinitions = useCallback(async () => {
    try {
      setDefinitions(await fetchDefinitions());
    } catch (e) {
      console.error(getErrorMessage(e));
    }
  }, []);

  const loadVersions = useCallback(async (definitionId: number) => {
    try {
      setVersions(await fetchVersions(definitionId));
    } catch (e) {
      console.error(getErrorMessage(e));
    }
  }, []);

  useEffect(() => {
    void loadDefinitions();
  }, [loadDefinitions]);

  useEffect(() => {
    if (selectedId != null) void loadVersions(selectedId);
  }, [selectedId, loadVersions]);

  const reloadVersions = useCallback(() => {
    if (selectedId != null) void loadVersions(selectedId);
  }, [selectedId, loadVersions]);

  const loadRuns = useCallback(async () => {
    try {
      setRuns(await fetchRuns(selectedId ?? undefined));
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  }, [selectedId]);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  const openRunDetail = async (threadId: string) => {
    try {
      setRunDetail(await fetchRunDetail(threadId));
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const doStartRun = async () => {
    if (selectedId == null) return;
    try {
      const params: unknown = JSON.parse(runParams);
      if (params === null || typeof params !== 'object' || Array.isArray(params)) {
        message.error('触发参数必须为 JSON 对象');
        return;
      }
      await startRun(selectedId, params as Record<string, unknown>);
      message.success('已发起工作流运行');
      void loadRuns();
    } catch (e) {
      message.error(e instanceof SyntaxError ? 'JSON 解析失败' : getErrorMessage(e));
    }
  };

  const doRunAction = async (run: WorkflowRunSummary, action: 'approve' | 'reject' | 'cancel') => {
    try {
      if (action === 'cancel') await cancelRun(run.thread_id);
      else await approveRun(run.thread_id, action === 'approve', '');
      message.success(action === 'cancel' ? '等待态已取消' : '审批已提交');
      void loadRuns();
      if (runDetail?.run.thread_id === run.thread_id) void openRunDetail(run.thread_id);
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const doCancelRunning = async (run: WorkflowRunSummary) => {
    try {
      await cancelRunningRun(run.thread_id);
      message.success('取消请求已提交（执行中节点不打断，后续节点停止推进）');
      void loadRuns();
      if (runDetail?.run.thread_id === run.thread_id) void openRunDetail(run.thread_id);
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const doRetryRun = async (run: WorkflowRunSummary) => {
    try {
      await retryRun(run.thread_id);
      message.success('重试已发起（已成功节点不重执行）');
      void loadRuns();
      if (runDetail?.run.thread_id === run.thread_id) void openRunDetail(run.thread_id);
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const openDetail = async (v: WorkflowVersion) => {
    try {
      setDetail(await fetchVersionDetail(v.id));
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const doCreateDefinition = async () => {
    const values = await form.validateFields();
    try {
      await createDefinition(values);
      message.success('流程定义已创建，请新建草稿版本');
      setDefModalOpen(false);
      form.resetFields();
      void loadDefinitions();
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const doCreateDraft = async () => {
    if (selectedId == null) return;
    try {
      const graph =
        draftTab === 'canvas'
          ? canvasValue
          : (JSON.parse(draftText) as { nodes: unknown[]; edges: unknown[] });
      await createDraftVersion(selectedId, {
        nodes: (graph.nodes ?? []) as unknown[],
        edges: (graph.edges ?? []) as unknown[],
      });
      message.success('草稿版本已创建');
      setDraftModalOpen(false);
      reloadVersions();
    } catch (e) {
      message.error(e instanceof SyntaxError ? 'JSON 解析失败，请检查格式' : getErrorMessage(e));
    }
  };

  const doExportDify = async (versionId: number) => {
    setDifyBusy(true);
    try {
      const result = await exportDifyDsl(versionId);
      setDifyExport(result);
      setDifyYaml(result.yaml);
      setDifyReport(null);
      setDifyModalOpen(true);
      setDifyTab('export');
    } catch (e) {
      message.error(getErrorMessage(e));
    } finally {
      setDifyBusy(false);
    }
  };

  const doReconcileDify = async (versionId: number) => {
    if (!difyYaml.trim()) {
      message.warning('请先粘贴 Dify DSL YAML');
      return;
    }
    setDifyBusy(true);
    try {
      setDifyReport(await reconcileDifyDsl(versionId, difyYaml));
    } catch (e) {
      message.error(getErrorMessage(e));
    } finally {
      setDifyBusy(false);
    }
  };

  const doImportDify = async () => {
    if (selectedId == null || !difyYaml.trim()) {
      message.warning('请先粘贴 Dify DSL YAML');
      return;
    }
    setDifyBusy(true);
    try {
      const result = await importDifyDsl(selectedId, difyYaml);
      message.success(`已导入为新草稿版本 v${result.version}（${result.node_count} 节点 / ${result.edge_count} 连线）`);
      setDifyModalOpen(false);
      setDifyYaml('');
      reloadVersions();
    } catch (e) {
      message.error(getErrorMessage(e));
    } finally {
      setDifyBusy(false);
    }
  };

  const downloadDifyYaml = () => {
    if (!difyExport) return;
    const blob = new Blob([difyExport.yaml], { type: 'application/x-yaml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${difyExport.app_name}.yml`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const doSubmit = async (v: WorkflowVersion) => {
    try {
      await submitVersion(v.id);
      message.success('已提交审核（提交人不可自行审核）');
      reloadVersions();
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const doApprove = async (v: WorkflowVersion) => {
    try {
      await reviewVersion(v.id, true);
      message.success('审核通过，版本已发布');
      reloadVersions();
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const doReject = async () => {
    if (!rejectTarget) return;
    try {
      await reviewVersion(rejectTarget.id, false, rejectNote || undefined);
      message.success('已驳回，退回草稿');
      setRejectTarget(null);
      setRejectNote('');
      reloadVersions();
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const doDeactivate = async (v: WorkflowVersion) => {
    try {
      await deactivateVersion(v.id);
      message.success('已停用（运行中实例不受影响）');
      reloadVersions();
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const doRollback = async (v: WorkflowVersion) => {
    if (selectedId == null) return;
    try {
      await rollbackDefinition(selectedId, v.id);
      message.success('已回滚：目标版本重新成为唯一发布版');
      reloadVersions();
    } catch (e) {
      message.error(getErrorMessage(e));
    }
  };

  const selected = definitions.find((d) => d.id === selectedId) ?? null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card
        title="流程定义"
        extra={
          <Space>
            <Button
              icon={<PlusOutlined />}
              onClick={() => setDefModalOpen(true)}
              data-testid="create-definition"
            >
              新建定义
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => void loadDefinitions()} />
          </Space>
        }
      >
        <Table<WorkflowDefinition>
          rowKey="id"
          size="small"
          dataSource={definitions}
          pagination={false}
          onRow={(record) => ({
            onClick: () => setSelectedId(record.id),
            style: { cursor: 'pointer', background: record.id === selectedId ? '#f0f5ff' : undefined },
          })}
          columns={[
            { title: 'Code', dataIndex: 'code' },
            { title: '名称', dataIndex: 'name' },
            { title: '说明', dataIndex: 'description', ellipsis: true },
            { title: '创建时间', dataIndex: 'created_at', render: fmt },
          ]}
        />
      </Card>

      <Card
        title={selected ? `版本管理 · ${selected.name}` : '版本管理（先选择一个定义）'}
        extra={
          <Space>
            <Button
              icon={<PlusOutlined />}
              disabled={selectedId == null}
              onClick={() => setDraftModalOpen(true)}
              data-testid="create-draft"
            >
              新建草稿版本
            </Button>
            <Button
              icon={<ReloadOutlined />}
              disabled={selectedId == null}
              onClick={reloadVersions}
            />
          </Space>
        }
      >
        {selectedId == null ? (
          <Text type="secondary">在上方点击一条流程定义后，这里显示其全部版本。</Text>
        ) : (
          <Table<WorkflowVersion>
            rowKey="id"
            size="small"
            dataSource={versions}
            pagination={false}
            columns={[
              { title: '版本', dataIndex: 'version', width: 64 },
              {
                title: '状态',
                dataIndex: 'status',
                width: 96,
                render: (s: WorkflowVersionStatus) => (
                  <Tag color={STATUS_TAG[s].color}>{STATUS_TAG[s].text}</Tag>
                ),
              },
              { title: '备注', dataIndex: 'note', ellipsis: true },
              { title: '提交时间', dataIndex: 'submitted_at', width: 150, render: fmt },
              { title: '审核意见', dataIndex: 'review_note', ellipsis: true },
              { title: '发布时间', dataIndex: 'published_at', width: 150, render: fmt },
              {
                title: '操作',
                width: 240,
                render: (_: unknown, v: WorkflowVersion) => (
                  <Space size={4} wrap>
                    <Button size="small" onClick={() => void openDetail(v)}>
                      查看
                    </Button>
                    {v.status === 'draft' && (
                      <Button size="small" type="primary" onClick={() => void doSubmit(v)}>
                        提交审核
                      </Button>
                    )}
                    {v.status === 'pending_review' && (
                      <>
                        <Button
                          size="small"
                          type="primary"
                          onClick={() => void doApprove(v)}
                          data-testid={`approve-${v.id}`}
                        >
                          通过
                        </Button>
                        <Button size="small" danger onClick={() => setRejectTarget(v)}>
                          驳回
                        </Button>
                      </>
                    )}
                    {v.status === 'published' && (
                      <Button size="small" danger onClick={() => void doDeactivate(v)}>
                        停用
                      </Button>
                    )}
                    {(v.status === 'published' || v.status === 'deprecated') && (
                      <Button size="small" onClick={() => void doRollback(v)}>
                        回滚到此版
                      </Button>
                    )}
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Card>

      <Card title="运行记录" extra={<Button icon={<ReloadOutlined />} onClick={() => void loadRuns()}>刷新</Button>}>
        <Space style={{ marginBottom: 12 }} wrap>
          <Input.TextArea
            rows={2}
            value={runParams}
            onChange={(e) => setRunParams(e.target.value)}
            placeholder="手动触发参数（JSON 对象）"
            style={{ width: 280, fontFamily: 'monospace' }}
          />
          <Button type="primary" disabled={selectedId == null} onClick={() => void doStartRun()}>
            手动触发所选流程
          </Button>
        </Space>
        <Table<WorkflowRunSummary>
          rowKey="id"
          size="small"
          dataSource={runs}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 70 },
            { title: '定义 ID', dataIndex: 'definition_id', width: 90 },
            { title: '版本 ID', dataIndex: 'version_id', width: 90 },
            { title: '触发', dataIndex: 'trigger_type', width: 80 },
            { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag>{s}</Tag> },
            { title: '错误', dataIndex: 'error', ellipsis: true },
            { title: '操作', render: (_: unknown, run: WorkflowRunSummary) => (
              <Space size={4} wrap>
                <Button size="small" onClick={() => void openRunDetail(run.thread_id)}>详情</Button>
                {run.status === 'waiting' && (
                  <>
                    <Button size="small" type="primary" onClick={() => void doRunAction(run, 'approve')}>同意</Button>
                    <Button size="small" onClick={() => void doRunAction(run, 'reject')}>驳回</Button>
                    <Button size="small" danger onClick={() => void doRunAction(run, 'cancel')}>取消等待</Button>
                  </>
                )}
                {run.status === 'running' && (
                  <Button size="small" danger onClick={() => void doCancelRunning(run)}>取消运行</Button>
                )}
                {run.status === 'failed' && (
                  <Button size="small" type="primary" onClick={() => void doRetryRun(run)}>重试</Button>
                )}
              </Space>
            ) },
          ]}
        />
      </Card>

      <Modal
        title={runDetail ? `运行详情 · #${runDetail.run.id}` : '运行详情'}
        open={runDetail != null}
        footer={null}
        onCancel={() => setRunDetail(null)}
        width={760}
      >
        {runDetail && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text>状态：{runDetail.run.status} · 触发：{runDetail.run.trigger_type}</Text>
            {runDetail.run.error && <Text type="danger">错误：{runDetail.run.error}</Text>}
            <Table
              rowKey={(step) => `${step.node_key}-${step.attempt}`}
              size="small"
              pagination={false}
              dataSource={runDetail.steps}
              columns={[
                { title: '节点', dataIndex: 'node_key' },
                { title: '类型', dataIndex: 'node_type' },
                { title: '尝试', dataIndex: 'attempt' },
                { title: '状态', dataIndex: 'status' },
                { title: '错误', dataIndex: 'error' },
              ]}
            />
          </Space>
        )}
      </Modal>

      <Modal
        title="新建流程定义"
        open={defModalOpen}
        onOk={() => void doCreateDefinition()}
        onCancel={() => setDefModalOpen(false)}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="code"
            label="Code（小写蛇形，唯一）"
            rules={[{ required: true, message: '请输入 code' }]}
          >
            <Input placeholder="hire_review" />
          </Form.Item>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="招聘复核流程" />
          </Form.Item>
          <Form.Item name="description" label="说明（可选）">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="新建草稿版本（画布编辑，服务端校验）"
        open={draftModalOpen}
        onOk={() => void doCreateDraft()}
        onCancel={() => setDraftModalOpen(false)}
        okText="创建草稿"
        cancelText="取消"
        width={1160}
      >
        <Tabs
          activeKey={draftTab}
          onChange={setDraftTab}
          items={[
            {
              key: 'canvas',
              label: '画布编辑',
              children: (
                <GraphCanvas
                  nodeTypes={nodeTypes}
                  value={canvasValue}
                  onChange={setCanvasValue}
                />
              ),
            },
            {
              key: 'json',
              label: 'JSON 输入（高级）',
              children: (
                <>
                  <Text type="secondary">
                    nodes：key/name/node_type（白名单六类）+ inputs/outputs 引用；edges：source/target/branch
                    （人工审批出边必须 approve/reject）。服务端整图校验兜底。
                  </Text>
                  <Input.TextArea
                    rows={14}
                    value={draftText}
                    onChange={(e) => setDraftText(e.target.value)}
                    style={{ marginTop: 12, fontFamily: 'monospace' }}
                  />
                </>
              ),
            },
          ]}
        />
      </Modal>

      <Modal
        title={`Dify DSL · ${difyExport?.app_name ?? ''}`}
        open={difyModalOpen}
        footer={null}
        onCancel={() => setDifyModalOpen(false)}
        width={760}
      >
        <Tabs
          activeKey={difyTab}
          onChange={setDifyTab}
          items={[
            {
              key: 'export',
              label: '导出 DSL',
              children: (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <Text type="secondary">
                    节点 {difyExport?.node_count ?? '—'} / 连线 {difyExport?.edge_count ?? '—'}｜DSL 版本以导出内容为准
                  </Text>
                  <Button onClick={downloadDifyYaml} disabled={!difyExport} data-testid="download-dsl">
                    下载 .yml
                  </Button>
                  <Input.TextArea
                    rows={12}
                    readOnly
                    value={difyExport?.yaml ?? ''}
                    style={{ fontFamily: 'monospace' }}
                  />
                </div>
              ),
            },
            {
              key: 'reconcile',
              label: '版本对账',
              children: (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <Text type="secondary">
                    粘贴 Dify 侧导出的 DSL，与本落库版本对账（identical=一致；差异列 added/removed/changed）。
                  </Text>
                  <Input.TextArea
                    rows={10}
                    value={difyYaml}
                    onChange={(e) => setDifyYaml(e.target.value)}
                    style={{ fontFamily: 'monospace' }}
                    placeholder="粘贴 Dify DSL YAML"
                  />
                  <Button
                    loading={difyBusy}
                    onClick={() => detail && void doReconcileDify(detail.version.id)}
                    data-testid="reconcile-dsl"
                  >
                    对账
                  </Button>
                  {difyReport && (
                    <pre style={{ background: '#fafafa', padding: 8, fontSize: 12, maxHeight: 220, overflow: 'auto' }}>
                      {JSON.stringify(difyReport, null, 2)}
                    </pre>
                  )}
                </div>
              ),
            },
            {
              key: 'import',
              label: '显式导入',
              children: (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <Text type="secondary">
                    导入为新草稿版本（画布外改动需显式导入；已发布版本不受影响）。
                  </Text>
                  <Input.TextArea
                    rows={10}
                    value={difyYaml}
                    onChange={(e) => setDifyYaml(e.target.value)}
                    style={{ fontFamily: 'monospace' }}
                    placeholder="粘贴 Dify DSL YAML"
                  />
                  <Button
                    type="primary"
                    loading={difyBusy}
                    onClick={() => void doImportDify()}
                    data-testid="import-dsl"
                  >
                    导入为新草稿版本
                  </Button>
                </div>
              ),
            },
          ]}
        />
      </Modal>

      <Modal
        title={`驳回 v${rejectTarget?.version ?? ''}（退回草稿）`}
        open={rejectTarget != null}
        onOk={() => void doReject()}
        onCancel={() => setRejectTarget(null)}
        okText="确认驳回"
        cancelText="取消"
      >
        <Input.TextArea
          rows={3}
          maxLength={500}
          placeholder="审核意见（可选，≤500 字）"
          value={rejectNote}
          onChange={(e) => setRejectNote(e.target.value)}
        />
      </Modal>

      <Modal
        title={detail ? `版本详情 · v${detail.version.version}（${STATUS_TAG[detail.version.status].text}）` : '版本详情'}
        open={detail != null}
        footer={null}
        onCancel={() => setDetail(null)}
        width={760}
      >
        {detail && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Space size={8} wrap>
              <Text type="secondary">提交人 {detail.version.submitted_by ?? '—'}</Text>
              <Text type="secondary">提交 {fmt(detail.version.submitted_at)}</Text>
              <Text type="secondary">发布 {fmt(detail.version.published_at)}</Text>
              <Button
                size="small"
                icon={<LinkOutlined />}
                data-testid="dify-dsl"
                loading={difyBusy}
                onClick={() => void doExportDify(detail.version.id)}
              >
                Dify DSL（导出/对账/导入）
              </Button>
            </Space>
            {detail.version.review_note && (
              <Text type="warning">审核意见：{detail.version.review_note}</Text>
            )}
            <Text strong>节点（白名单六类）</Text>
            <Table
              rowKey="node_key"
              size="small"
              pagination={false}
              dataSource={detail.nodes}
              columns={[
                { title: 'Key', dataIndex: 'node_key' },
                { title: '名称', dataIndex: 'name' },
                { title: '类型', dataIndex: 'node_type' },
                {
                  title: '输入引用',
                  dataIndex: 'inputs',
                  render: (v: Array<Record<string, unknown>>) => JSON.stringify(v),
                  ellipsis: true,
                },
                {
                  title: '输出声明',
                  dataIndex: 'outputs',
                  render: (v: Array<Record<string, unknown>>) => JSON.stringify(v),
                  ellipsis: true,
                },
              ]}
            />
            <Text strong>连线</Text>
            <Table
              rowKey={(e) => `${e.source}->${e.target}-${e.branch ?? ''}`}
              size="small"
              pagination={false}
              dataSource={detail.edges}
              columns={[
                { title: '来源', dataIndex: 'source' },
                { title: '目标', dataIndex: 'target' },
                { title: '分支', dataIndex: 'branch', render: (b: string | null) => b || '—' },
              ]}
            />
          </div>
        )}
      </Modal>
    </div>
  );
}
