"""L1 单元测试 · 请求日志脱敏（todolist T-02 验收口径，T-01 先落地工具）。"""

from __future__ import annotations

from app.utils.mask import mask_email, mask_phone, mask_sensitive


def test_phone_mask():
    assert mask_phone("13800000000") == "138****0000"
    assert mask_sensitive("用户 13800000000 登录") == "用户 138****0000 登录"


def test_phone_embedded_in_long_number_not_masked():
    assert mask_sensitive("订单号 12313800000000999") == "订单号 12313800000000999"


def test_email_mask():
    assert mask_email("alice", "example.com") == "a***e@example.com"
    assert mask_sensitive("mail=alice@example.com") == "mail=a***e@example.com"


def test_mixed_text():
    text = "phone=13800000000&email=bob@corp.com&status=ok"
    masked = mask_sensitive(text)
    assert "138****0000" in masked
    assert "13800000000" not in masked
    assert "b***b@corp.com" in masked
    assert "status=ok" in masked


def test_empty_and_plain_text():
    assert mask_sensitive("") == ""
    assert mask_sensitive("GET /api/health") == "GET /api/health"
