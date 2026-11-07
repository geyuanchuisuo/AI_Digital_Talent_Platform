'use client';

/**
 * 登录页视图（T-08）。
 *
 * 对接 `POST /api/auth/login`（接口 §2）：成功返回 `{token, expires_in, user}`，
 * token 存 localStorage，随后跳到 `?redirect=` 指定页（缺省 `/screen`）。
 *
 * 三个要点：
 *  1. **不重复提示错误**：401/403/网络异常的文案由 `lib/http.ts` 统一弹（口径一致），
 *     这里只把 401 额外标到密码框上，方便用户直接改；
 *  2. **防开放重定向**：`redirect` 只接受站内绝对路径（`/` 开头且不是 `//`），
 *     否则一律回 `/screen`；
 *  3. **已登录直接放行**：本地 token 未过期就不再让用户重登。
 */

import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input, Typography, message } from 'antd';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { getToken, getUser, isTokenExpired, setAuth } from '@/lib/auth';
import { ApiError, post } from '@/lib/http';
import type { AuthUser } from '@/lib/auth';

interface LoginFormValues {
  username: string;
  password: string;
}

interface LoginResult {
  token: string;
  expires_in: number;
  user: AuthUser;
}

/**
 * `reason` → 登录页顶部提示条。
 * 为什么不用 antd message：被动跳转（过期 / 停用）往往伴随整页 `location.replace`，
 * message 挂在旧文档上，跳完就消失，用户根本来不及看；提示条随页面持久存在才叫"有提示"。
 */
const REASON_TIPS: Record<string, { type: 'warning' | 'error'; text: string }> = {
  expired: { type: 'warning', text: '登录已过期，请重新登录' },
  disabled: { type: 'error', text: '账号已停用，请联系管理员' },
};

function safeRedirect(raw: string | null): string | null {
  if (!raw) return null;
  if (!raw.startsWith('/') || raw.startsWith('//')) return null;
  return raw;
}

/**
 * 双端分流（2026-11-07，功能 4）：显式 `?redirect=` 优先（回跳原页面）；
 * 否则按角色分发——`personal` 进个人端首页 `/home`，管理侧保持 `/screen`。
 */
function resolveTarget(redirectParam: string | null, role?: string): string {
  const explicit = safeRedirect(redirectParam);
  if (explicit) return explicit;
  return role === 'personal' ? '/home' : '/screen';
}

export default function LoginView({
  redirectParam,
  reasonParam = null,
}: {
  redirectParam: string | null;
  reasonParam?: string | null;
}) {
  const router = useRouter();
  const [form] = Form.useForm<LoginFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const target = resolveTarget(redirectParam, getUser()?.role);
  const tip = reasonParam ? REASON_TIPS[reasonParam] : undefined;
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // 已登录（token 未过期）直接跳走，避免重复登录
  useEffect(() => {
    const token = getToken();
    if (token && !isTokenExpired(token)) {
      router.replace(target);
    }
  }, [router, target]);

  const handleFinish = async (values: LoginFormValues) => {
    setSubmitting(true);
    try {
      const data = await post<LoginResult>('/auth/login', {
        username: values.username.trim(),
        password: values.password,
      });
      setAuth(data.token, data.user);
      message.success(`欢迎回来，${data.user.display_name || data.user.username}`);
      router.replace(resolveTarget(redirectParam, data.user.role));
    } catch (error) {
      if (mountedRef.current) setSubmitting(false);
      if (error instanceof ApiError && error.code === 401) {
        form.setFields([{ name: 'password', errors: [error.message] }]);
      }
      // 其余错误（403 停用 / 网络异常 / 500）已由 http 层统一提示，这里不重复弹
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, #e6f0ff 0%, #f5f7fa 55%, #eef2ff 100%)',
        padding: 24,
      }}
    >
      <Card style={{ width: 400, boxShadow: '0 12px 32px rgba(22, 119, 255, 0.08)' }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            AI 数字化人才平台
          </Typography.Title>
          <Typography.Text type="secondary">简历筛选 · 人才画像 · 岗位匹配</Typography.Text>
        </div>

        {tip && (
          <Alert
            type={tip.type}
            message={tip.text}
            showIcon
            closable
            style={{ marginBottom: 16 }}
          />
        )}

        <Form form={form} layout="vertical" onFinish={handleFinish} requiredMark={false} size="large">
          <Form.Item
            name="username"
            label="账号"
            rules={[{ required: true, message: '请输入账号' }]}
          >
            <Input prefix={<UserOutlined />} placeholder="请输入账号" autoComplete="username" autoFocus />
          </Form.Item>

          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="请输入密码"
              autoComplete="current-password"
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 12 }}>
            <Button type="primary" htmlType="submit" block loading={submitting}>
              登录
            </Button>
          </Form.Item>
        </Form>

        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          初始账号由 init_db.py 生成：hr / hr123456
        </Typography.Text>
      </Card>
    </div>
  );
}
