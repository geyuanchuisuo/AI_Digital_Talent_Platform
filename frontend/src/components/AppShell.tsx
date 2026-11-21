'use client';

/**
 * 主布局（T-06）：左侧四个业务菜单 + 顶部当前用户/退出。
 *
 * 菜单严格对应《需求文档》§6 的 5 个页面（登录页不在布局内）：
 * 简历智能筛选 / 人才画像档案 / 岗位条件配置 / 岗位-人才匹配。
 * 各页面在 B3~B5 逐个落地，当前均为占位页（不写业务字段，避免接口未定先猜）。
 */

import {
  ApartmentOutlined,
  AuditOutlined,
  BarChartOutlined,
  BellOutlined,
  CalendarOutlined,
  CarOutlined,
  DashboardOutlined,
  FileSearchOutlined,
  FundOutlined,
  LineChartOutlined,
  LogoutOutlined,
  ProfileOutlined,
  ReadOutlined,
  RobotOutlined,
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
  // AI 面试间（REQ-C-20261121-133109-A 第 2 项，C 域页面；子路径 key 须排在 /interviews 前）
  { key: '/interviews/ai', icon: <RobotOutlined />, label: 'AI 面试间' },
  { key: '/interviews', icon: <SolutionOutlined />, label: '人工面试' },
  // 培训报名（REQ-C-20261121-133109-A 第 2 项，C 域页面；子路径 key 须排在 /training 前）
  { key: '/training/enrollments', icon: <CalendarOutlined />, label: '培训报名' },
  { key: '/training', icon: <ReadOutlined />, label: '培训成长' },
  // 人才盘点（REQ-C-20261121-133109-A 第 2 项，C 域页面）
  { key: '/inventory', icon: <FundOutlined />, label: '人才盘点' },
  // D 模块（T-D20 AI 中台 + T-D22 管理员工作台 + T-D24 通知中心）
  { key: '/dashboard', icon: <DashboardOutlined />, label: '工作台' },
  { key: '/ai-platform', icon: <SettingOutlined />, label: 'AI 中台' },
  { key: '/notifications', icon: <BellOutlined />, label: '通知中心' },
  // 数据分析 / 流程定义（REQ-C-20261121-133109-A 第 1/2 项，D 域页面）
  { key: '/analytics', icon: <LineChartOutlined />, label: '数据分析' },
  { key: '/workflows', icon: <ApartmentOutlined />, label: '流程定义' },
];

/**
 * 页头标题映射（REQ-C-20261121-133109-A 第 1 项）：pathname 精确匹配优先，
 * 覆盖无菜单页面与子路径（如 /training/enrollments 应显示「培训报名」而非
 * 「培训成长」）；未命中回退 selectedKey 对应菜单 label。
 */
const PAGE_TITLES: Record<string, string> = {
  '/inventory': '人才盘点',
  '/analytics': '数据分析',
  '/workflows': '流程定义',
  '/training/enrollments': '培训报名',
  '/interviews/ai': 'AI 面试间',
};

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
    // 精确匹配优先；子路径命中最长 key（如 /training/enrollments → 培训报名而非培训成长）
    const exact = MENU_ITEMS.find((item) => pathname === item.key);
    if (exact) return exact.key;
    const child = MENU_ITEMS.filter((item) => pathname.startsWith(`${item.key}/`)).sort(
      (a, b) => b.key.length - a.key.length,
    )[0];
    return child?.key ?? '/screen';
  }, [pathname]);

  // 页头标题：pathname 精确映射（无菜单页面/子路径）优先，回退选中菜单 label
  const pageTitle = useMemo(() => {
    return PAGE_TITLES[pathname] ?? MENU_ITEMS.find((item) => item.key === selectedKey)?.label ?? '';
  }, [pathname, selectedKey]);

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
            {pageTitle}
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
