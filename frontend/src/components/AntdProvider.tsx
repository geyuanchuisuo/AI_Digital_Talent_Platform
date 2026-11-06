'use client';

/**
 * antd 5 在 Next.js App Router 下的 Provider（T-06）。
 *
 * 两件事必须一起做，否则会出现"首屏无样式"或"刷新闪烁"：
 *   1. `StyleProvider + createCache + useServerInsertedHTML`：把 antd 的 CSS-in-JS
 *      样式注入 SSR HTML（官方 Next.js App Router 方案）；
 *   2. `ConfigProvider locale=zhCN`：分页/日期/空状态等内置文案走中文。
 *
 * `AntdApp` 提供 message/notification 的上下文容器；`lib/http.ts` 里用的是静态
 * `message`，两者互不冲突（静态调用会自建容器，仅在需要主题联动时才必须换成 useApp）。
 */

import { App as AntdApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { createCache, extractStyle, StyleProvider } from '@ant-design/cssinjs';
import { useServerInsertedHTML } from 'next/navigation';
import { useMemo } from 'react';

type Cache = ReturnType<typeof createCache>;

export default function AntdProvider({ children }: { children: React.ReactNode }) {
  const cache = useMemo<Cache>(() => createCache(), []);

  useServerInsertedHTML(() => (
    <style
      id="antd-cssinjs"
      // extractStyle(cache, true) 会清空已注入部分，避免重复输出
      dangerouslySetInnerHTML={{ __html: extractStyle(cache, true) }}
    />
  ));

  return (
    <StyleProvider cache={cache}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: '#1677ff',
            borderRadius: 6,
          },
        }}
      >
        <AntdApp>{children}</AntdApp>
      </ConfigProvider>
    </StyleProvider>
  );
}
