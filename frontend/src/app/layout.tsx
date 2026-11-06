import type { Metadata } from 'next';

import AntdProvider from '@/components/AntdProvider';
import 'antd/dist/reset.css';
import '@/styles/globals.css';

export const metadata: Metadata = {
  title: 'AI 数字化人才平台',
  description: '简历智能筛选 · 人才画像档案 · 岗位-人才匹配',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <AntdProvider>{children}</AntdProvider>
      </body>
    </html>
  );
}
