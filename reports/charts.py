"""Small server-rendered SVG charts. Colours come from CSS classes (light and dark themes), values
are always written next to the marks, and every mark has a hover tooltip (<title>)."""

from django.utils.html import escape
from django.utils.safestring import mark_safe

ROW = 26  # height of one horizontal bar row
LABEL_W = 130
BAR_W = 190
VALUE_W = 70


def hbars(items, unit="", width=BAR_W, max_value=None, tone="bar"):
    """Horizontal bars, one series: items = [(label, value, tooltip or None, link or None)]."""
    if not items:
        return ""
    top = max_value or max((v for _, v, *_ in items), default=0) or 1
    h = ROW * len(items)
    total_w = LABEL_W + width + VALUE_W
    out = [f'<svg class="chart" viewBox="0 0 {total_w} {h}" width="{total_w}" height="{h}" role="img" aria-label="Bar chart">']
    for i, (label, value, tip, *rest) in enumerate(items):
        link = rest[0] if rest else None
        y = i * ROW
        w = max(2, round(width * (value / top))) if value else 0
        title = escape(tip or f"{label}: {value}{unit}")
        bar = f'<rect class="{tone}" x="{LABEL_W}" y="{y + 6}" width="{w}" height="{ROW - 12}" rx="4"><title>{title}</title></rect>'
        hit = f'<rect class="hit" x="0" y="{y}" width="{total_w}" height="{ROW}"><title>{title}</title></rect>'
        text = (
            f'<text class="label" x="{LABEL_W - 10}" y="{y + ROW / 2 + 4}" text-anchor="end">{escape(_short(label))}</text>'
            f'<text class="value" x="{LABEL_W + w + 8}" y="{y + ROW / 2 + 4}">{escape(_fmt(value))}{escape(unit)}</text>'
        )
        row = bar + text + hit
        out.append(f'<a href="{escape(link)}">{row}</a>' if link else row)
    out.append("</svg>")
    return mark_safe("".join(out))


def stacked(parts, width=BAR_W + LABEL_W):
    """One 100% bar split into labelled status parts: parts = [(label, value, tone)]; legend below."""
    total = sum(v for _, v, _ in parts)
    if not total:
        return ""
    out = [f'<svg class="chart" viewBox="0 0 {width} 22" width="100%" role="img" aria-label="Share by status">']
    x = 0.0
    for label, value, tone in parts:
        if not value:
            continue
        w = width * value / total
        out.append(f'<rect class="{tone}" x="{x:.1f}" y="2" width="{max(w - 2, 1):.1f}" height="18" rx="4"><title>{escape(label)}: {value} ({round(100 * value / total)}%)</title></rect>')
        x += w
    out.append("</svg>")
    legend = "".join(
        f'<span class="legend-item"><span class="swatch {tone}"></span>{escape(label)} <strong>{value}</strong> ({round(100 * value / total)}%)</span>'
        for label, value, tone in parts
    )
    return mark_safe("".join(out) + f'<div class="legend">{legend}</div>')


def columns(items, height=120, unit=""):
    """Vertical columns (e.g. how many people have 0, 1, 2… duties): items = [(label, value)]."""
    if not items:
        return ""
    top = max(v for _, v in items) or 1
    col_w, gap = 34, 6
    w = len(items) * (col_w + gap)
    out = [f'<svg class="chart" viewBox="0 0 {w} {height + 34}" width="{w}" height="{height + 34}" role="img" aria-label="Column chart">']
    for i, (label, value) in enumerate(items):
        x = i * (col_w + gap)
        h = round(height * value / top) if value else 0
        y = height - h + 14
        out.append(
            f'<rect class="bar" x="{x}" y="{y}" width="{col_w}" height="{max(h, 1 if value else 0)}" rx="4"><title>{escape(str(label))}: {value}{escape(unit)}</title></rect>'
            f'<text class="value" x="{x + col_w / 2}" y="{y - 4}" text-anchor="middle">{value}</text>'
            f'<text class="label" x="{x + col_w / 2}" y="{height + 30}" text-anchor="middle">{escape(str(label))}</text>'
        )
    out.append("</svg>")
    return mark_safe("".join(out))


def _short(text, n=20):
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _fmt(value):
    if isinstance(value, float):
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return str(value)
