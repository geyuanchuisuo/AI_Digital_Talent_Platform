/**
 * 画像详情视图（T-21，需求 F2-4 / F2-6 / F2-7 / 接口 §5）。
 *
 * 口径：
 * 1. **经历类只读（D-J）**：教育/工作/项目/证书只展示，全页不出现任何经历编辑输入，
 *    PATCH 请求也永远不带这四个键。
 * 2. `vector_status=failed` → 顶部告警条 + "重建向量"按钮（revector，503 时提示稍后重试）。
 * 3. 标签展示 tag_type/source；技能行蓝色（随 skills 管理）；`manual_edited_fields`
 *    的字段在信息卡里打"已人工修正"标记。
 * 4. 原文下载走 blob（downloadFile 带 Bearer）；`<a>` 直链会 401。
 * 5. 删除为软删（Popconfirm 二次确认），成功后回列表页。
 * 6. 本画像作为画像 A 出现在 pending 查重里时，顶部出告警条，弹窗并排对比处理。
 * 7. 顶部提供「返回人才画像列表」出口（`router.push('/talents')`），换人查看不必依赖浏览器后退。
 */

'use client';

import { ArrowLeftOutlined, DownloadOutlined, EditOutlined, TagsOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Popconfirm,
  Row,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { getErrorMessage } from '@/lib/http';
import { getUser } from '@/lib/auth';
import SourceEvidence from '@/features/talents/SourceEvidence';
import ProfileQuestion from '@/features/talents/ProfileQuestion';

import { deleteTalent, downloadResumeFile, fetchDuplicates, getTalent, revectorTalent } from '../api';
import type { DuplicateItem, TalentDetailData } from '../types';
import { formatTime, formatYears, vectorStatusMeta } from '../utils';
import AbilityRadar from './AbilityRadar';
import DuplicateResolveModal from './DuplicateResolveModal';
import EditProfileModal from './EditProfileModal';
import TagEditModal from './TagEditModal';

const { Text, Title } = Typography;

/** D-J：经历类字段一期只读（用于自检与文档提示，不参与任何请求） */
export const READONLY_EXPERIENCE_KEYS = ['educations', 'employments', 'projects', 'certificates'] as const;

const VERDICT_META: Record<string, { label: string; color: string }> = {
  passed: { label: '通过', color: 'success' },
  pending: { label: '待定', color: 'warning' },
  rejected: { label: '淘汰', color: 'error' },
};

export default function DetailView({ talentId }: { talentId: number }) {
  const router = useRouter();
  const [detail, setDetail] = useState<TalentDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [revecting, setRevecting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [pendingDup, setPendingDup] = useState<DuplicateItem | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [tagEditOpen, setTagEditOpen] = useState(false);
  const [dupModalOpen, setDupModalOpen] = useState(false);
  const [personalAccess, setPersonalAccess] = useState(false);

  const load = useCallback(async () => {
    const user = getUser();
    if (user?.role === 'personal') {
      setPersonalAccess(true);
      if (user.person_id === talentId) router.replace('/my-profile');
      return;
    }
    setLoading(true);
    try {
      const data = await getTalent(talentId);
      setDetail(data);
      // 本画像作为画像 A 的 pending 查重（详情页入口）
      try {
        const dup = await fetchDuplicates(1, 50, 'pending');
        setPendingDup(dup.items.find((item) => item.profile_a?.id === talentId) ?? null);
      } catch {
        // 查重列表失败不阻塞详情
      }
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [talentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleRevector = async () => {
    setRevecting(true);
    try {
      const result = await revectorTalent(talentId);
      if (result.vector_status === 'synced') {
        message.success('向量已重建');
      } else {
        message.warning(`向量状态：${result.vector_status}`);
      }
      void load();
    } catch (error) {
      // 503 = 向量服务不可用（无业务写入接口的合法失败），提示稍后重试
      message.error(`${getErrorMessage(error)}（可稍后重试，或运行 rebuild_vectors.py 全量补偿）`);
    } finally {
      setRevecting(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await deleteTalent(talentId);
      message.success('已删除（可在列表页回收站恢复）');
      router.push('/talents');
    } catch (error) {
      message.error(getErrorMessage(error));
      setDeleting(false);
    }
  };

  const handleDownload = async (resumeFileId: number, filename: string) => {
    try {
      await downloadResumeFile(talentId, resumeFileId, filename);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  if (personalAccess) return <Alert type="warning" message="请从个人端的我的档案入口查看本人资料，无权访问他人管理档案。" />;
  if (loading && !detail) {
    return (
      <div style={{ padding: 80, textAlign: 'center' }}>
        <Spin size="large" />
      </div>
    );
  }
  if (!detail) {
    return <Empty description="画像不存在或已删除" />;
  }

  const profile = detail.profile;
  const vecMeta = vectorStatusMeta(profile.vector_status);
  const protectedFields = profile.manual_edited_fields ?? [];
  const tagColor = (source: string, tagType: string) =>
    tagType === 'skill' ? 'blue' : source === 'manual' ? 'geekblue' : 'green';

  const mark = (field: string) =>
    protectedFields.includes(field) ? (
      <Tooltip title="该字段已被人工修正，后续自动解析不会覆盖">
        <Tag color="blue" style={{ marginLeft: 4 }}>
          人工
        </Tag>
      </Tooltip>
    ) : null;

  const experienceTabs = [
    {
      key: 'employments',
      label: `工作经历（${detail.employments.length}）`,
      children: detail.employments.length ? (
        <Timeline
          items={detail.employments.map((job) => ({
            children: (
              <div>
                <Text strong>{job.company ?? '—'}</Text> · {job.title ?? '—'}
                <Text type="secondary">
                  {' '}
                  （{job.start ?? '?'} ~ {job.end ?? '至今'}）
                </Text>
                {job.description ? (
                  <div>
                    <Text type="secondary">{String(job.description)}</Text>
                  </div>
                ) : null}
              </div>
            ),
          }))}
        />
      ) : (
        <Empty description="无工作经历" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ),
    },
    {
      key: 'projects',
      label: `项目（${detail.projects.length}）`,
      children: detail.projects.length ? (
        <Timeline
          items={detail.projects.map((project) => ({
            children: (
              <div>
                <Text strong>{project.name ?? '—'}</Text>
                {project.role ? ` · ${project.role}` : ''}
                <Text type="secondary">
                  {' '}
                  （{project.start ?? '?'} ~ {project.end ?? '至今'}）
                </Text>
                {project.description ? (
                  <div>
                    <Text type="secondary">{String(project.description)}</Text>
                  </div>
                ) : null}
              </div>
            ),
          }))}
        />
      ) : (
        <Empty description="无项目" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ),
    },
    {
      key: 'educations',
      label: `教育（${detail.educations.length}）`,
      children: detail.educations.length ? (
        <Timeline
          items={detail.educations.map((edu) => ({
            children: (
              <div>
                <Text strong>{edu.school ?? '—'}</Text> · {edu.major ?? '—'} · {edu.degree ?? '—'}
                <Text type="secondary">
                  {' '}
                  （{edu.start ?? '?'} ~ {edu.end ?? '至今'}）
                </Text>
              </div>
            ),
          }))}
        />
      ) : (
        <Empty description="无教育经历" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ),
    },
    {
      key: 'certificates',
      label: `证书（${detail.certificates.length}）`,
      children: detail.certificates.length ? (
        <Space wrap size={4}>
          {detail.certificates.map((cert, index) => (
            <Tag key={`${cert.name ?? index}-${index}`}>{String(cert.name ?? '未知证书')}</Tag>
          ))}
        </Space>
      ) : (
        <Empty description="无证书" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {/* 详情页唯一的列表出口：跳回 /talents 再选其他人，避免只能用浏览器后退 */}
      <div>
        <Button
          type="link"
          icon={<ArrowLeftOutlined />}
          style={{ paddingLeft: 0 }}
          onClick={() => router.push('/talents')}
        >
          返回人才画像列表
        </Button>
      </div>
      {profile.vector_status === 'failed' && (
        <Alert
          type="warning"
          showIcon
          message="向量同步失败：语义检索与向量相关功能暂不可用"
          description="画像数据本身完好；点击重建向量修复（Embedding/Milvus 不可用时会提示稍后重试）。"
          action={
            <Button size="small" danger loading={revecting} onClick={() => void handleRevector()}>
              重建向量
            </Button>
          }
        />
      )}
      {pendingDup && (
        <Alert
          type="info"
          showIcon
          message="该画像有待确认的重复简历"
          description={`新简历「${pendingDup.parsed_resume?.filename ?? '—'}」与本画像命中查重（${duplicateReasonLabel(pendingDup.reason)}），请确认合并或保留。`}
          action={
            <Button
              size="small"
              type="primary"
              onClick={() => setDupModalOpen(true)}
            >
              去处理
            </Button>
          }
        />
      )}

      <Card
        title={
          <Space wrap>
            <Title level={4} style={{ margin: 0 }}>
              {profile.name}
            </Title>
            <Text type="secondary">{profile.current_title ?? '—'}</Text>
            <Tag color={vecMeta.color}>{vecMeta.label}</Tag>
          </Space>
        }
        extra={
          <Space wrap>
            <Button icon={<EditOutlined />} onClick={() => setEditOpen(true)}>
              编辑基本信息
            </Button>
            <Button icon={<TagsOutlined />} onClick={() => setTagEditOpen(true)}>
              编辑标签
            </Button>
            <Popconfirm
              title="删除该画像？"
              description="软删除：原始简历与历史结果保留，可在列表页回收站恢复。"
              okText="确定删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => void handleDelete()}
            >
              <Button danger loading={deleting}>
                删除
              </Button>
            </Popconfirm>
          </Space>
        }
      >
        <Row gutter={24}>
          <Col xs={24} md={14}>
            <Descriptions
              size="small"
              column={2}
              items={[
                { key: 'phone', label: <span>手机号{mark('phone')}</span>, children: profile.phone ?? '—' },
                { key: 'email', label: <span>邮箱{mark('email')}</span>, children: profile.email ?? '—' },
                { key: 'city', label: <span>城市{mark('city')}</span>, children: profile.city ?? '—' },
                {
                  key: 'degree',
                  label: <span>学历{mark('degree')}</span>,
                  children: profile.degree ?? '—',
                },
                {
                  key: 'work_years',
                  label: <span>年限{mark('work_years')}</span>,
                  children: formatYears(profile.work_years),
                },
                {
                  key: 'expected_title',
                  label: <span>期望职位{mark('expected_title')}</span>,
                  children: profile.expected_title ?? '—',
                },
                {
                  key: 'birth',
                  label: '出生年份',
                  children: profile.birth_year ?? '—',
                },
                { key: 'created', label: '建档', children: formatTime(profile.created_at) },
                { key: 'updated', label: '更新', children: formatTime(profile.updated_at) },
              ]}
            />
            <div style={{ marginTop: 12 }}>
              <Space wrap size={4}>
                {(profile.tags ?? []).map((row) => (
                  <Tooltip
                    key={row.tag}
                    title={`类型：${row.tag_type} · 来源：${row.source === 'manual' ? '人工' : 'AI'}`}
                  >
                    <Tag color={tagColor(row.source, row.tag_type)}>{row.tag}</Tag>
                  </Tooltip>
                ))}
                {(profile.tags ?? []).length === 0 && <Text type="secondary">标签后台生成中…</Text>}
              </Space>
            </div>
          </Col>
          <Col xs={24} md={10}>
            <Card size="small" title="能力雷达" styles={{ body: { paddingTop: 8 } }}>
              {profile.ability_scores ? (
                <AbilityRadar scores={profile.ability_scores} />
              ) : (
                <Empty description="能力维度后台生成中" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </Card>
          </Col>
        </Row>
      </Card>

      <Card title="经历与历史（经历类由解析结果聚合，只读）">
        <Tabs
          defaultActiveKey="experience"
          items={[
            {
              key:'evidence', label:'原文依据',
              children:<><SourceEvidence personId={talentId} refreshKey={detail.profile.updated_at || ''} /><ProfileQuestion personId={talentId} allowIndex /></>,
            },
            {
              key: 'experience',
              label: '经历',
              children: (
                <Tabs type="card" items={experienceTabs} />
              ),
            },
            {
              key: 'resume_files',
              label: `原始简历（${detail.resume_files.length}）`,
              children: detail.resume_files.length ? (
                <Space direction="vertical" size={4}>
                  {detail.resume_files.map((file) => (
                    <Space key={file.resume_file_id}>
                      <Text>{file.filename}</Text>
                      <Text type="secondary" copyable={{ text: file.upload_batch_id }}>
                        批次 {file.upload_batch_id.slice(0, 8)}…
                      </Text>
                      <Button
                        size="small"
                        icon={<DownloadOutlined />}
                        onClick={() => void handleDownload(file.resume_file_id, file.filename)}
                      >
                        下载原文
                      </Button>
                    </Space>
                  ))}
                </Space>
              ) : (
                <Empty description="无关联简历" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ),
            },
            {
              key: 'screen_history',
              label: `筛选历史（${detail.screen_history.length}）`,
              children: detail.screen_history.length ? (
                <Table
                  rowKey={(row) => `${row.batch_id}-${row.created_at}`}
                  size="small"
                  pagination={false}
                  dataSource={detail.screen_history}
                  columns={[
                    { title: '批次', dataIndex: 'batch_id', width: 80 },
                    { title: '岗位', dataIndex: 'job_title', render: (v) => v ?? '—' },
                    {
                      title: '有效结论',
                      dataIndex: 'effective_verdict',
                      width: 110,
                      render: (v: string) => {
                        const meta = VERDICT_META[v] ?? { label: v, color: 'default' };
                        return <Tag color={meta.color}>{meta.label}</Tag>;
                      },
                    },
                    { title: '得分', dataIndex: 'score', width: 90, render: (v) => v ?? '—' },
                    { title: '时间', dataIndex: 'created_at', width: 150, render: (v) => formatTime(v) },
                  ]}
                />
              ) : (
                <Empty description="暂无筛选记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ),
            },
            {
              key: 'match_history',
              label: `匹配历史（${detail.match_history.length}）`,
              children: detail.match_history.length ? (
                <Table
                  rowKey={(row) => `${row.batch_id}-${row.created_at}`}
                  size="small"
                  pagination={false}
                  dataSource={detail.match_history}
                  columns={[
                    { title: '批次', dataIndex: 'batch_id', width: 80 },
                    { title: '岗位', dataIndex: 'job_title', render: (v) => v ?? '—' },
                    { title: '匹配分', dataIndex: 'score', width: 90 },
                    {
                      title: '硬门槛',
                      dataIndex: 'hard_fail',
                      width: 90,
                      render: (v: boolean) => (v ? <Tag color="error">不通过</Tag> : <Tag color="success">通过</Tag>),
                    },
                    { title: '时间', dataIndex: 'created_at', width: 150, render: (v) => formatTime(v) },
                  ]}
                />
              ) : (
                <Empty description="暂无匹配记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ),
            },
          ]}
        />
      </Card>

      <EditProfileModal
        open={editOpen}
        profile={profile}
        onClose={() => setEditOpen(false)}
        onPatched={() => void load()}
      />
      <TagEditModal
        open={tagEditOpen}
        profile={profile}
        onClose={() => setTagEditOpen(false)}
        onPatched={() => void load()}
      />
      <DuplicateResolveModal
        open={dupModalOpen}
        item={pendingDup}
        onClose={() => setDupModalOpen(false)}
        onResolved={() => void load()}
      />
    </Space>
  );
}

function duplicateReasonLabel(reason: string): string {
  return (
    { name_phone_match: '姓名+手机号相同', phone_match: '手机号相同', email_match: '邮箱相同' }[reason] ??
    reason
  );
}
