import { afterEach } from 'vitest';
import { cleanup, configure } from '@testing-library/react';

// React 18 的 act 环境标记（vitest 不自动设置，否则 renderHook 会告警）
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Ant Design 的表格/弹窗在 Windows jsdom 中可能超过默认 1s；仅调整等待上限，
// 条件满足即返回，不增加固定延时，也不忽略未处理异常或失败断言。
configure({ asyncUtilTimeout: 5000 });

// jsdom 缺这两个浏览器 API，antd 的响应式组件（Menu/Sider/Grid）会直接报错
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

afterEach(() => {
  cleanup();
});
