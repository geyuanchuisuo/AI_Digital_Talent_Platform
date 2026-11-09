/**
 * 画像列表面板（T-20，需求 F2-3 / 接口 §5）。
 *
 * 口径：
 * 1. **检索模式切换**：关键词（`GET /talents?keyword=`，走 MySQL LIKE）/ 语义
 *    （`GET /talents/search`，走向量，带 similarity 列、无分页）。语义接口 503
 *    时提示"向量服务不可用"，不按空态渲染。
 * 2. **列表不轮询**：画像数据低频变化，改用"筛选变化/手动/动作后"刷新
 *    （区别于 screen 页的 2s 轮询——队列有状态流转才需要）。
 *    文本类筛选（关键词/标签）回车或点搜索才生效；离散控件（下拉/年限/回收站）
 *    改变即生效。
 * 3. **回收站**：`deleted=true` 只看已软删画像，行内显示删除时间 + "恢复"按钮；
 *    恢复后留在回收站视角刷新（列表与角标同步）。
 * 4. 过滤条件是服务端语义：tag 为精确匹配、degree 为六档枚举，前端不做模糊化。
 */

'use client';

import { ReloadOutlined, UndoOutlined } from '@ant-design/icons';
import {
  Badge,
  Button,
  Card,
  Empty,
  Input,
  InputNumber,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { getErrorMessage } from '@/lib/http';
import BatchProfileQuestion from '@/features/talents/BatchProfileQuestion';

import { fetchTalents, restoreTalent, searchTalents } from './api';
import type { TalentListItem, TalentSearchItem } from './types';
import { DEGREE_OPTIONS, TAG_TYPE_OPTIONS, formatSimilarity, formatTime, formatYears, vectorStatusMeta } from './utils';

const { Text } = Typography;
const PAGE_SIZE = 10;

type SearchMode = 'keyword' | 'semantic';

type TableRow = Partial<TalentListItem & TalentSearchItem> & {
  id: number;
  name: string;
  similarity?: number;
};

interface AppliedFilters {
  mode: SearchMode;
  keyword: string;
  tag?: string;
  tagType?: string;
  degree?: string;
  minYears?: number;
  maxYears?: number;
  recycle: boolean;
}

const INITIAL_FILTERS: AppliedFilters = { mode: 'keyword', keyword: '', recycle: false };

export interface TalentListPanelProps {
  /** 查重待处理数（父组件拉取，角标显示） */
  duplicateCount: number;
  onOpenDuplicates: () => void;
  /** 列表变动（恢复/查重处理）后通知父组件刷新角标 */
  onChanged: () => void;
}

export default function TalentListPanel({ duplicateCount, onOpenDuplicates, onChanged }: TalentListPanelProps) {
  const [filters, setFilters] = useState<AppliedFilters>(INITIAL_FILTERS);
  const [draftKeyword, setDraftKeyword] = useState('');
  const [draftTag, setDraftTag] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [rows, setRows] = useState<TableRow[]>([]);
  const [selected,setSelected]=useState<number[]>([]);
  useEffect(()=>{setSelected([]);},[rows,filters.recycle]);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState<number | null>(null);
  const [reloadSignal, setReloadSignal] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (filters.mode === 'semantic') {
        const items = filters.keyword ? await searchTalents(filters.keyword, 20) : [];
        setRows(items);
        setTotal(items.length);
      } else {
        const data = await fetchTalents({
          keyword: filters.keyword || undefined,
          tag: filters.tag,
          tag_type: filters.tagType,
          degree: filters.degree,
          min_years: filters.minYears,
          max_years: filters.maxYears,
          deleted: filters.recycle || undefined,
          page,
          page_size: PAGE_SIZE,
        });
        setRows(data.items);
        setTotal(data.total);
      }
    } catch (error) {
      // 语义检索 503（向量服务不可用）给出业务化提示；其余由 lib/http 统一弹
      if (filters.mode === 'semantic') {
        setRows([]);
        setTotal(0);
        message.warning(`语义检索暂不可用：${getErrorMessage(error)}`);
      }
    } finally {
      setLoading(false);
    }
  }, [filters, page]);

  useEffect(() => {
    void load();
  }, [load, reloadSignal]);

  /** 文本筛选（关键词/标签）：回车或点搜索时提交；并重置页码 */
  const applyTextFilters = () => {
    setPage(1);
    setFilters((prev) => ({
      ...prev,
      keyword: draftKeyword.trim(),
      tag: draftTag.trim() || undefined,
    }));
  };

  /** 离散筛选：改动即生效 */
  const patchFilters = (patch: Partial<AppliedFilters>) => {
    setPage(1);
    setFilters((prev) => ({ ...prev, ...patch }));
  };

  const handleRestore = async (id: number) => {
    setRestoring(id);
    try {
      await restoreTalent(id);
      message.success('已恢复，向量重建中（失败可在详情页重试）');
      onChanged();
      setReloadSignal((value) => value + 1);
    } catch (error) {
      message.error(getErrorMessage(error));
    } finally {
      setRestoring(null);
    }
  };

  const semantic = filters.mode === 'semantic';

  const columns: ColumnsType<TableRow> = [
    {
      title: '姓名',
      dataIndex: 'name',
      width: 110,
      render: (name: string, row) =>
        filters.recycle ? (
          <Text type="secondary">{name}</Text>
        ) : (
          <Link href={`/talents/${row.id}`}>{name}</Link>
        ),
    },
    { title: '当前/最近职位', dataIndex: 'current_title', width: 140, render: (v) => v ?? '—' },
    { title: '年限', dataIndex: 'work_years', width: 80, render: (v) => formatYears(v) },
    { title: '学历', dataIndex: 'degree', width: 90, render: (v) => v ?? '—' },
    { title: '城市', dataIndex: 'city', width: 80, render: (v) => v ?? '—' },
    ...(semantic
      ? [
          {
            title: '相似度',
            dataIndex: 'similarity',
            width: 80,
            render: (v?: number) => (v === undefined ? '—' : formatSimilarity(v)),
          } as ColumnsType<TableRow>[number],
        ]
      : []),
    {
      title: '标签',
      dataIndex: 'tags',
      render: (tags: string[] | undefined, row) => {
        if (!tags || tags.length === 0) return '—';
        const shown = tags.slice(0, 5);
        const rest = (row.tag_total ?? tags.length) - shown.length;
        return (
          <Space wrap size={4}>
            {shown.map((t) => (
              <Tag key={t}>{t}</Tag>
            ))}
            {rest > 0 && <Tag>+{rest}</Tag>}
          </Space>
        );
      },
    },
    {
      title: '向量状态',
      dataIndex: 'vector_status',
      width: 110,
      render: (v: string | undefined) => {
        if (!v) return '—';
        const meta = vectorStatusMeta(v);
        const tip = v === 'failed' ? '可在画像详情页点击"重建向量"补偿' : undefined;
        return (
          <Tooltip title={tip}>
            <Tag color={meta.color}>{meta.label}</Tag>
          </Tooltip>
        );
      },
    },
    ...(filters.recycle
      ? [
          {
            title: '删除时间',
            dataIndex: 'deleted_at',
            width: 140,
            render: (v: string | null | undefined) => formatTime(v),
          } as ColumnsType<TableRow>[number],
        ]
      : []),
    { title: '更新时间', dataIndex: 'updated_at', width: 140, render: (v) => formatTime(v) },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_, row) =>
        filters.recycle ? (
          <Popconfirm
            title="恢复该画像？"
            description="恢复后向量将按需重建，重新进入默认列表与筛选范围。"
            okText="确定"
            cancelText="取消"
            onConfirm={() => void handleRestore(row.id)}
          >
            <Button size="small" icon={<UndoOutlined />} loading={restoring === row.id}>
              恢复
            </Button>
          </Popconfirm>
        ) : (
          <Link href={`/talents/${row.id}`}>查看详情</Link>
        ),
    },
  ];

  return (
    <Card
      title="人才画像"
      extra={
        <Space wrap>
          <Badge count={duplicateCount} color="#d48806" offset={[-4, 2]}>
            <Button onClick={onOpenDuplicates}>查重待确认</Button>
          </Badge>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => setReloadSignal((value) => value + 1)}
            loading={loading}
          >
            刷新
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space wrap>
          <Segmented
            value={filters.mode}
            onChange={(value) => {
              setDraftKeyword('');
              setDraftTag('');
              patchFilters({ mode: value as SearchMode, keyword: '' });
            }}
            options={[
              { label: '关键词', value: 'keyword' },
              { label: '语义检索', value: 'semantic' },
            ]}
          />
          <Input.Search
            allowClear
            style={{ width: 320 }}
            placeholder={semantic ? '用自然语言描述想找的人' : '姓名 / 职位 / 技能（模糊）'}
            value={draftKeyword}
            onChange={(event) => setDraftKeyword(event.target.value)}
            onSearch={applyTextFilters}
          />
        </Space>
        <Space wrap>
          <Input
            allowClear
            style={{ width: 160 }}
            placeholder="标签（精确匹配）"
            value={draftTag}
            onChange={(event) => setDraftTag(event.target.value)}
            onPressEnter={applyTextFilters}
            onBlur={applyTextFilters}
            disabled={semantic}
          />
          <Select
            allowClear
            style={{ width: 120 }}
            placeholder="标签类型"
            options={TAG_TYPE_OPTIONS}
            value={filters.tagType}
            onChange={(value) => patchFilters({ tagType: value })}
            disabled={semantic}
          />
          <Select
            allowClear
            style={{ width: 120 }}
            placeholder="学历"
            options={DEGREE_OPTIONS.map((value) => ({ value, label: value }))}
            value={filters.degree}
            onChange={(value) => patchFilters({ degree: value })}
            disabled={semantic}
          />
          <Space.Compact>
            <InputNumber
              min={0}
              max={80}
              placeholder="年限≥"
              style={{ width: 90 }}
              value={filters.minYears}
              onChange={(value) => patchFilters({ minYears: value ?? undefined })}
              disabled={semantic}
            />
            <InputNumber
              min={0}
              max={80}
              placeholder="年限≤"
              style={{ width: 90 }}
              value={filters.maxYears}
              onChange={(value) => patchFilters({ maxYears: value ?? undefined })}
              disabled={semantic}
            />
          </Space.Compact>
          <Text type={semantic ? 'secondary' : undefined}>回收站</Text>
          <Switch
            checked={filters.recycle}
            onChange={(value) => patchFilters({ recycle: value })}
            disabled={semantic}
          />
        </Space>
        <Table<TableRow>
          rowKey="id"
          rowSelection={filters.recycle?undefined:{selectedRowKeys:selected,
            onChange:keys=>setSelected(keys.map(Number)),
            getCheckboxProps:row=>({disabled:selected.length>=20&&!selected.includes(row.id)})}}
          loading={loading}
          columns={columns}
          dataSource={rows}
          locale={{
            emptyText: <Empty description={semantic ? '输入描述后检索，或切换关键词模式' : '暂无画像'} />,
          }}
          pagination={
            semantic
              ? false
              : {
                  current: page,
                  pageSize: PAGE_SIZE,
                  total,
                  showSizeChanger: false,
                  showTotal: (value) => `共 ${value} 条`,
                  onChange: (next) => setPage(next),
                }
          }
          scroll={{ x: 900 }}
        />
        <BatchProfileQuestion people={rows.filter(r=>selected.includes(r.id)).map(r=>({id:r.id,name:r.name}))}/>
      </Space>
    </Card>
  );
}
