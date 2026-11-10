"""图表渲染（PRD D16）——图表规格 → PNG（Pillow 自绘，零新增依赖）。

规格来源：`analytics.charts.build_chart`（number / bar / none 三型；数据只从同一
响应 table 派生）。本渲染器是**纯函数**：同输入字节级同输出（PNG 编码不含时间戳），
不触库、不重算——同快照导出的可复现基础；导出前重新问数仍由 T-D11 哨兵断言把关。

中文：按候选路径探测 CJK 字体（Windows 微软雅黑/黑体、Linux 文泉驿/Noto、
macOS 苹方），全部缺失时退回 PIL 默认位图字体（中文无法渲染但不崩溃）——
部署环境须保证任一候选字体存在（边界登记于 D 域更新记录）。

能力边界：bar（含截断标注、负值基线）/ number 两型；none 与未知类型返回 None
（导出物不嵌图，与 T-D11 行为一致）。自由问数（nl2sql）结果产出 chart 规格后
自动复用本渲染器（D14 范围）。
"""

from __future__ import annotations

import io
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# 绘图常量（px）
_BAR_W, _BAR_TITLE_H, _BAR_TOP, _BAR_BOTTOM, _BAR_LEFT, _BAR_RIGHT = 920, 52, 64, 56, 90, 36
_BAR_MAX = 20          # 与 analytics.charts._MAX_SERIES 一致的渲染上限
_BAR_SLOT_MIN = 34
_CARD_W, _CARD_H = 640, 280
_COLORS = {
    "bar": (78, 121, 165),       # #4E79A7
    "grid": (221, 221, 221),
    "text": (51, 51, 51),
    "muted": (120, 120, 120),
    "bg": (255, 255, 255),
}

_CJK_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",                                     # 微软雅黑（Windows）
    r"C:\Windows\Fonts\simhei.ttf",                                   # 黑体（Windows）
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",                   # 文泉驿（Linux）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",         # Noto CJK（Linux）
    "/System/Library/Fonts/PingFang.ttc",                             # 苹方（macOS）
)


@lru_cache(maxsize=1)
def _font_path() -> str | None:
    """探测第一个可用的 CJK 字体文件；全缺返回 None。"""
    for path in _CJK_FONT_CANDIDATES:
        try:
            ImageFont.truetype(path, 12)
            return path
        except Exception:  # noqa: BLE001 字体损坏/不存在一律尝试下一个
            continue
    return None


@lru_cache(maxsize=16)
def _font(size: int):
    path = _font_path()
    if path is None:
        return ImageFont.load_default()
    return ImageFont.truetype(path, size)


def _fmt_value(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v) if v is not None else "-"
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return f"{f:.2f}"


def _label_text(raw: Any) -> str:
    text = str(raw if raw is not None else "-")
    return text if len(text) <= 8 else text[:7] + "…"


def render_chart_png(chart: dict | None, *, title: str = "") -> bytes | None:
    """图表规格 → PNG bytes；none/未知类型/缺数据返回 None（调用方不嵌图）。"""
    if not isinstance(chart, dict):
        return None
    chart_type = chart.get("chart_type")
    if chart_type == "bar" and chart.get("data"):
        return _render_bar(chart, title)
    if chart_type == "number" and chart.get("value") is not None:
        return _render_number(chart, title)
    return None


def _render_bar(chart: dict, title: str) -> bytes:
    series = [p for p in chart["data"] if isinstance(p, dict)][: _BAR_MAX]
    if not series:
        return _render_number({"label": title, "value": None, "chart_type": "number"}, title)

    values: list[float] = []
    for p in series:
        try:
            values.append(float(p.get("value")))
        except (TypeError, ValueError):
            values.append(0.0)
    names = [_label_text(p.get("name")) for p in series]

    n = len(series)
    slot = max(_BAR_SLOT_MIN, (_BAR_W - _BAR_LEFT - _BAR_RIGHT) // max(n, 1))
    width = _BAR_LEFT + slot * n + _BAR_RIGHT
    height = _BAR_TOP + 320 + _BAR_BOTTOM
    img = Image.new("RGB", (width, height), _COLORS["bg"])
    draw = ImageDraw.Draw(img)
    font_title, font_text, font_small = _font(16), _font(12), _font(10)

    if title:
        draw.text((16, 14), title, fill=_COLORS["text"], font=font_title)

    plot_top, plot_bottom = _BAR_TOP + 8, height - _BAR_BOTTOM
    plot_h = plot_bottom - plot_top
    vmax, vmin = max(values, default=1.0), min(0.0, min(values, default=0.0))
    if vmax <= vmin:
        vmax = vmin + 1.0

    def y_of(v: float) -> int:
        return int(plot_top + (vmax - v) / (vmax - vmin) * plot_h)

    # 横向网格 + 刻度值（5 档）
    for i in range(5):
        v = vmin + (vmax - vmin) * i / 4
        y = y_of(v)
        draw.line((_BAR_LEFT, y, width - _BAR_RIGHT, y), fill=_COLORS["grid"], width=1)
        draw.text((8, y - 6), _fmt_value(v), fill=_COLORS["muted"], font=font_small)

    # 基线（0 值）
    y_zero = y_of(0)
    draw.line((_BAR_LEFT, y_zero, width - _BAR_RIGHT, y_zero), fill=_COLORS["text"], width=1)

    bar_w = int(slot * 0.62)
    for i, v in enumerate(values):
        x0 = _BAR_LEFT + slot * i + (slot - bar_w) // 2
        y = y_of(v)
        top, bottom = (y, y_zero) if v >= 0 else (y_zero, y)
        draw.rectangle((x0, top, x0 + bar_w, max(bottom, top + 1)), fill=_COLORS["bar"])
        # 数值标在柱端外侧
        vy = y - 16 if v >= 0 else y + 4
        draw.text((x0 + bar_w // 2 - 14, vy), _fmt_value(v), fill=_COLORS["text"], font=font_small)
        # 类目标签
        draw.text((x0, plot_bottom + 8), names[i], fill=_COLORS["text"], font=font_text)

    if chart.get("truncated_series"):
        note = "（仅显示前 %d 项，数据已截断）" % len(series)
        draw.text((width - _BAR_RIGHT - 220, height - 22), note,
                  fill=_COLORS["muted"], font=font_small)

    return _to_png(img)


def _render_number(chart: dict, title: str) -> bytes:
    label = str(chart.get("label") or title or "")
    value = _fmt_value(chart.get("value"))
    img = Image.new("RGB", (_CARD_W, _CARD_H), _COLORS["bg"])
    draw = ImageDraw.Draw(img)
    draw.rectangle((4, 4, _CARD_W - 5, _CARD_H - 5), outline=_COLORS["grid"], width=1)
    draw.text((24, 24), label, fill=_COLORS["muted"], font=_font(18))
    draw.text((24, _CARD_H // 2 - 44), value, fill=_COLORS["text"], font=_font(56))
    return _to_png(img)


def _to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
