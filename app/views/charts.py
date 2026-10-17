"""Small server-side SVG geometry for the dashboards (update 50): sparklines, donut gauges, multi-line charts and
stacked bars. Templates draw the SVG; the page's CSP allows SVG attributes (no inline style attribute is needed)."""


def _nums(values):
    return [float(v) for v in values if v is not None]


def spark(values, w=120, h=34, pad=3, lo=None, hi=None):
    """{'line', 'area', 'x', 'y'} path data of a sparkline, or None with fewer than 2 values."""
    vals = _nums(values)
    if len(vals) < 2:
        return None
    lo = min(vals) if lo is None else lo
    hi = max(vals) if hi is None else hi
    span = (hi - lo) or 1.0
    step = (w - 2 * pad) / (len(vals) - 1)
    pts = [(pad + i * step, pad + (hi - v) / span * (h - 2 * pad)) for i, v in enumerate(vals)]
    line = "M" + " L".join("%.1f,%.1f" % p for p in pts)
    area = line + " L%.1f,%.1f L%.1f,%.1f Z" % (pts[-1][0], h, pts[0][0], h)
    return {"line": line, "area": area, "x": round(pts[-1][0], 1), "y": round(pts[-1][1], 1), "w": w, "h": h}


def donut(pct, r=30, width=8):
    """Circle gauge: {'r', 'c' (circumference), 'dash' "filled rest", 'size', 'cx'} for 0-100 %."""
    pct = max(0.0, min(100.0, float(pct or 0)))
    c = 2 * 3.14159265 * r
    size = 2 * (r + width)
    return {"r": r, "w": width, "c": round(c, 2), "dash": "%.2f %.2f" % (c * pct / 100.0, c), "size": size, "cx": size / 2.0, "pct": pct}


def lines(series, w=520, h=150, lo=0.0, hi=100.0, m=(30, 8, 10, 18)):
    """Several lines on one y-axis (e.g. disk / memory / CPU % over 24 h). series: [(key, [values])].
    Returns {'paths': [(key, d)], 'grid': [(y, label)], 'w', 'h', 'x0', 'x1'}."""
    left, right, top, bottom = m
    vals = [v for _k, vs in series for v in _nums(vs)]
    if not vals:
        return None
    hi = max(hi, max(vals))
    out = []
    for key, vs in series:
        vs = list(vs)
        if len(_nums(vs)) < 2:
            continue
        step = (w - left - right) / max(1, len(vs) - 1)
        d, pen = [], "M"
        for i, v in enumerate(vs):
            if v is None:
                pen = "M"
                continue
            d.append("%s%.1f,%.1f" % (pen, left + i * step, top + (hi - float(v)) / ((hi - lo) or 1) * (h - top - bottom)))
            pen = "L"
        out.append((key, " ".join(d)))
    grid = [(round(top + (hi - g) / ((hi - lo) or 1) * (h - top - bottom), 1), int(g)) for g in (lo, (lo + hi) / 2, hi)]
    return {"paths": out, "grid": grid, "w": w, "h": h, "x0": left, "x1": w - right, "bottom": h - bottom}


def bars(values, w=240, h=40, gap=2):
    """Mini column chart: [(x, y, width, height, value)] scaled to the largest value."""
    vals = [float(v or 0) for v in values]
    if not vals:
        return []
    top = max(vals) or 1.0
    bw = max(1.0, (w - gap * (len(vals) - 1)) / len(vals))
    return [(round(i * (bw + gap), 1), round(h - max(1.0, v / top * h) if v else h - 1, 1), round(bw, 1),
             round(max(1.0, v / top * h) if v else 1, 1), v) for i, v in enumerate(vals)]


def stack(parts, total=None):
    """Widths in % of a stacked bar: [(key, value, pct)]."""
    total = total if total is not None else sum(v for _k, v in parts)
    return [(k, v, round(v / total * 100, 2) if total else 0) for k, v in parts]


def register(app):
    app.jinja_env.globals.update(svg_spark=spark, svg_donut=donut, svg_lines=lines, svg_bars=bars, svg_stack=stack)
