'use client';

/**
 * 主布局（T-06）：左侧四个业务菜单 + 顶部当前用户/退出。
 *
 * 菜单严格对应《需求文档》§6 的 5 个页面（登录页不在布局内）：
 * 简历智能筛选 / 人才画像档案 / 岗位条件配置 / 岗位-人才匹配。
 * 各页面在 B3~B5 逐个落地，当前均为占位页（不写业务字段，避免接口未定先猜）。
 */

import {
  AuditOutlined,
  BarChartOutlined,
  BellOutlined,
  CarOutlined,
  DashboardOutlined,
  FileSearchOutlined,
  LogoutOutlined,
  ProfileOutlined,
  ReadOutlined,
  SolutionOutlined,
  TeamOutlined,
  SettingOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Avatar, Dropdown, Layout, Menu, Space, Typography } from 'antd';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';

import { clearAuth, getUser, type AuthUser } from '@/lib/auth';

const { Header, Sider, Content } = Layout;

const MENU_ITEMS = [
  { key: '/screen', icon: <FileSearchOutlined />, label: '简历筛选' },
  { key: '/talents', icon: <TeamOutlined />, label: '人才画像' },
  { key: '/knowledge', icon: <ReadOutlined />, label: '知识库' },
  { key: '/job-profiles', icon: <ProfileOutlined />, label: '岗位条件' },
  // 人才需求与缺口（T-A21/T-A24，A 侧 M3-V2.6）
  { key: '/demands', icon: <CarOutlined />, label: '人才需求' },
  { key: '/match', icon: <BarChartOutlined />, label: '岗位匹配' },
  // C 模块三域（C 请求 §5.3，页面已位于 app/(main)/ 下）
  { key: '/assessments', icon: <AuditOutlined />, label: '岗位测评' },
  { key: '/interviews', icon: <SolutionOutlined />, label: '人工面试' },
  { key: '/training', icon: <ReadOutlined />, label: '培训成长' },
  // D 模块（T-D20 AI 中台 + T-D22 管理员工作台 + T-D24 通知中心）
  { key: '/dashboard', icon: <DashboardOutlined />, label: '工作台' },
  { key: '/ai-platform', icon: <SettingOutlined />, label: 'AI 中台' },
  { key: '/notifications', icon: <BellOutlined />, label: '通知中心' },
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() || '';
  const [collapsed, setCollapsed] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);

  // localStorage 只能在客户端读，挂载后再取，避免 SSR 水合不一致
  useEffect(() => {
    setUser(getUser());
  }, []);

  const selectedKey = useMemo(() => {
    const hit = MENU_ITEMS.find((item) => pathname === item.key || pathname.startsWith(`${item.key}/`));
    return hit?.key ?? '/screen';
  }, [pathname]);

  const handleLogout = () => {
    clearAuth();
    router.replace('/login');
  };

  return (
    <Layout className="app-shell" style={{ minHeight: '100vh' }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed} breakpoint="lg" onBreakpoint={setCollapsed} collapsedWidth={0} theme="light" width={208}>
        <div
          style={{
            height: 56,
            display: 'flex',
            alignItems: 'center',
            justifyContent: collapsed ? 'center' : 'flex-start',
            paddingInline: collapsed ? 0 : 20,
            fontWeight: 600,
            fontSize: collapsed ? 14 : 16,
            color: '#1677ff',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
          }}
        >
          {collapsed ? 'AI' : 'AI 人才平台'}
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={MENU_ITEMS}
          onClick={({ key }) => router.push(key)}
          style={{ borderInlineEnd: 0 }}
        />
      </Sider>

      <Layout style={{minWidth:0}}>
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
          <Typography.Text strong style={{ fontSize: 15 }}>
            {MENU_ITEMS.find((item) => item.key === selectedKey)?.label ?? ''}
          </Typography.Text>

          <Dropdown
            trigger={['click']}
            menu={{
              items: [{ key: 'logout', icon: <LogoutOutlined />, label: '退出登录' }],
              onClick: ({ key }) => {
                if (key === 'logout') handleLogout();
              },
            }}
          >
            <Space style={{ cursor: 'pointer' }}>
              <Avatar size="small" icon={<UserOutlined />} />
              <span>{user?.display_name || user?.username || '未登录'}</span>
            </Space>
          </Dropdown>
        </Header>

        <Content style={{ padding: 24, minWidth:0 }}>{children}</Content>
      </Layout>
    </Layout>
  );
}
