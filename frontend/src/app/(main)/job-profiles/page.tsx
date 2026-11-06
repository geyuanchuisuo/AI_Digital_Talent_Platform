/**
 * 岗位条件配置页（T-26，需求 F3-1）。
 *
 * 口径：
 * 1. 列表展示 version / is_active（软删岗位可见，is_active=false 标记）；
 * 2. 删除：软删提示"有历史批次引用，岗位停用但可回溯"；物理删除直接移除；
 * 3. 编辑后 version+1，列表即时刷新。
 */

'use client';

import { PlusOutlined } from '@ant-design/icons';
import {
  Button,
  Card,
  Input,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useState } from 'react';

import { getErrorMessage } from '@/lib/http';

import { deleteJobProfile, fetchJobProfiles, getJobProfile } from './api';
import JobProfileFormModal from './JobProfileFormModal';
import type { JobProfileListItem, JobProfileOut } from './types';

const { Text } = Typography;
const PAGE_SIZE = 10;

export default function JobProfilesPage() {
  const [keyword, setKeyword] = useState('');
  const [appliedKeyword, setAppliedKeyword] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [rows, setRows] = useState<JobProfileListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<JobProfileOut | null>(null);
  const [reloadSignal, setReloadSignal] = useState(0);
  const [deleting, setDeleting] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchJobProfiles({
        keyword: appliedKeyword || undefined,
        page,
        page_size: PAGE_SIZE,
      });
      setRows(data.items);
      setTotal(data.total);
    } catch {
      // 统一错误提示由 lib/http 拦截器负责
    } finally {
      setLoading(false);
    }
  }, [appliedKeyword, page]);

  useEffect(() => {
    void load();
  }, [load, reloadSignal]);

  const openEdit = async (id: number) => {
    try {
      setEditing(await getJobProfile(id));
      setModalOpen(true);
    } catch (error) {
      message.error(getErrorMessage(error));
    }
  };

  const handleDelete = async (id: number) => {
    setDeleting(id);
    try {
      const result = await deleteJobProfile(id);
      if (result.soft_deleted) {
        message.info('该岗位有历史批次引用，已停用（is_active=false），历史批次仍可回看');
      } else {
        message.success('已删除');
      }
      setReloadSignal((value) => value + 1);
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setDeleting(null);
    }
  };

  const columns: ColumnsType<JobProfileListItem> = [
    {
      title: '岗位名称',
      dataIndex: 'title',
      render: (v, row) => <a onClick={() => void openEdit(row.id)}>{v}</a>,
    },
    { title: '部门', dataIndex: 'department', width: 110, render: (v) => v ?? '—' },
    { title: '最低学历', dataIndex: 'min_degree', width: 90, render: (v) => v ?? '不限' },
    {
      title: '最低年限',
      dataIndex: 'min_work_years',
      width: 90,
      render: (v) => (v === null ? '不限' : `${v} 年`),
    },
    {
      title: '必备技能',
      dataIndex: 'must_skills',
      render: (skills: string[]) =>
        skills.length ? (
          <Space wrap size={4}>
            {skills.slice(0, 6).map((s) => (
              <Tag key={s}>{s}</Tag>
            ))}
            {skills.length > 6 && <Tag>+{skills.length - 6}</Tag>}
          </Space>
        ) : (
          '—'
        ),
    },
    { title: '版本', dataIndex: 'version', width: 70, render: (v) => <Text code>v{v}</Text> },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 90,
      render: (active: boolean) =>
        active ? <Tag color="success">启用</Tag> : <Tag color="default">已停用（有历史批次）</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 130,
      render: (_, row) => (
        <Space size={4}>
          <a onClick={() => void openEdit(row.id)}>编辑</a>
          <Popconfirm
            title="删除该岗位？"
            description="有历史批次引用时将停用（软删）；否则物理删除。"
            okText="确定"
            cancelText="取消"
            onConfirm={() => void handleDelete(row.id)}
          >
            <a style={{ color: '#cf1322' }} aria-label={`删除 ${row.title}`}>
              删除
            </a>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="岗位条件"
      extra={
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditing(null);
            setModalOpen(true);
          }}
        >
          新建岗位
        </Button>
      }
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Input.Search
          allowClear
          style={{ width: 320 }}
          placeholder="岗位名 / 部门（模糊）"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          onSearch={() => {
            setPage(1);
            setAppliedKeyword(keyword.trim());
          }}
        />
        <Table<JobProfileListItem>
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={rows}
          locale={{ emptyText: '暂无岗位，点击右上角新建' }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            showSizeChanger: false,
            showTotal: (value) => `共 ${value} 条`,
            onChange: (next) => setPage(next),
          }}
        />
      </Space>
      <JobProfileFormModal
        open={modalOpen}
        editing={editing}
        onClose={() => setModalOpen(false)}
        onSaved={() => setReloadSignal((value) => value + 1)}
      />
    </Card>
  );
}
