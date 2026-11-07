'use client';

/**
 * 个人端首页（2026-11-07，功能 4）：登录分流落点（role=personal → /home）。
 * 当前为骨架：展示本人账号信息与三个业务挂载占位卡；
 * B 的本人档案/原件组件、C 的学习/测评组件在此挂载（接口可见性走 AccountScope）。
 * 用户信息挂载后从 localStorage 读取（与 AppShell/PersonalShell 同款模式）。
 */

import { Card, Col, Descriptions, Row, Typography } from 'antd';
import Link from 'next/link';
import { useEffect, useState } from 'react';

import { getUser, type AuthUser } from '@/lib/auth';

const PLACEHOLDERS = [
  { key: 'profile', title: '我的档案', desc: '本人人才档案与原件查看（B 组件挂载点）' },
  { key: 'learning', title: '我的学习', desc: '必修课进度 / 培训计划（C 组件挂载点）' },
  { key: 'assessments', title: '我的测评', desc: '测评任务与结果查看（C 组件挂载点）' },
];

export default function PersonHomePage() {
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    setUser(getUser());
  }, []);

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        欢迎，{user?.display_name || user?.username || '未登录'}
      </Typography.Title>
      <Card title="我的账号" style={{ marginBottom: 24 }}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="用户名">{user?.username ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="角色">{user?.role ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="档案编号">
            {user?.person_id ?? '未绑定'}
          </Descriptions.Item>
        </Descriptions>
      </Card>
      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <Link href="/applications">
            <Card title="我的岗位申请" hoverable>
              <Typography.Text>查看本人各申请的对外进度（已提交/筛选中/待面试/待决定/已结束）</Typography.Text>
            </Card>
          </Link>
        </Col>
        {PLACEHOLDERS.map((item) => (
          <Col xs={24} md={8} key={item.key}>
            <Card title={item.title}>
              <Typography.Text type="secondary">{item.desc}</Typography.Text>
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  );
}
