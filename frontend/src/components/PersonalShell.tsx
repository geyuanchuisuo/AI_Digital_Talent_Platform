'use client';

/**
 * 个人端布局壳（2026-11-07，功能 4：双端入口骨架）。
 *
 * 与管理端 AppShell 分离：个人端没有管理菜单，只有顶部品牌条与当前用户信息；
 * B/C 的个人侧组件（本人档案、学习记录、测评等）挂在 (person) 分组的页面里，
 * 由本壳统一包裹。账号未绑定档案（person_id 为空）时展示醒目提示，
 * 不阻断页面（具体数据的可见性以后端 `get_account_scope` 为最终授权点）。
 *
 * localStorage 只能客户端读：与 AppShell 相同，挂载后再取，避免 SSR 水合不一致。
 */

import { Avatar, Layout, Space, Tag, Typography } from 'antd';
import { UserOutlined } from '@ant-design/icons';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import { clearAuth, getUser, type AuthUser } from '@/lib/auth';

const { Header, Content } = Layout;

// 个人端导航（页面级入口；B/C 的新页面在此追加）
const NAV_ITEMS = [
  { key: '/home', label: '首页' },
  { key: '/applications', label: '我的岗位申请' },
];

export default function PersonalShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() || '';
  const [user, setUser] = useState<AuthUser | null>(null);

  // localStorage 只能在客户端读，挂载后再取，避免 SSR 水合不一致
  useEffect(() => {
    setUser(getUser());
  }, []);

  const handleLogout = () => {
    clearAuth();
    router.replace('/login');
  };

  return (
    <Layout style={{ minHeight: '100vh', background: '#f5f7fa' }}>
      <Header
        style={{
          background: '#fff',
          paddingInline: 24,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: '1px solid #f0f0f0',
          height: 56,
          lineHeight: '56px',
        }}
      >
        <Space size={12}>
          <Typography.Text strong style={{ fontSize: 15 }}>
            个人端
          </Typography.Text>
          <Tag color="blue">员工 / 候选人自助入口</Tag>
          <Space size={4}>
            {NAV_ITEMS.map((item) => (
              <Link
                key={item.key}
                href={item.key}
                style={{
                  paddingInline: 12,
                  paddingBlock: 4,
                  borderRadius: 4,
                  fontWeight: pathname.startsWith(item.key) ? 600 : 400,
                  background: pathname.startsWith(item.key) ? '#e6f4ff' : 'transparent',
                  color: 'inherit',
                }}
              >
                {item.label}
              </Link>
            ))}
          </Space>
        </Space>
        <Space
          style={{ cursor: 'pointer' }}
          onClick={handleLogout}
          data-testid="personal-logout"
        >
          <Avatar size="small" icon={<UserOutlined />} />
          <span>{user?.display_name || user?.username || '未登录'}</span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            退出登录
          </Typography.Text>
        </Space>
      </Header>
      <Content style={{ padding: 24 }}>
        {user && user.role === 'personal' && !user.person_id && (
          <Typography.Paragraph type="warning">
            当前账号尚未绑定人才档案，部分个人数据不可见；请联系 HR 完成绑定。
          </Typography.Paragraph>
        )}
        {children}
      </Content>
    </Layout>
  );
}
