'use client';

/**
 * 业务页占位（T-06 用）。
 *
 * B1 只交付"壳"，四个业务页面在 B3~B5 陆续实现。占位页显式写出**依赖任务**，
 * 让接力的人一眼看到该页由谁填，而不是误以为功能已废弃。
 * 这里刻意不渲染任何假数据/假表格——避免与真实接口字段产生"看起来对"的错觉。
 */

import { Alert, Card, List, Typography } from 'antd';

export interface PagePlaceholderProps {
  title: string;
  description: string;
  /** 该页依赖的后端任务与能力，如 ['T-14 解析队列', 'T-15 筛选批次'] */
  dependsOn: string[];
}

export default function PagePlaceholder({ title, description, dependsOn }: PagePlaceholderProps) {
  return (
    <Card>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        {title}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 20 }}>
        {description}
      </Typography.Paragraph>

      <Alert
        type="info"
        showIcon
        message="页面骨架已就绪，业务功能待后续任务接入"
        description="菜单、路由守卫、请求封装与轮询能力已可用；下方任务完成后本页替换为真实交互。"
        style={{ marginBottom: 20 }}
      />

      <Typography.Text strong>依赖任务</Typography.Text>
      <List
        size="small"
        dataSource={dependsOn}
        renderItem={(item) => <List.Item>{item}</List.Item>}
        style={{ marginTop: 8 }}
      />
    </Card>
  );
}
