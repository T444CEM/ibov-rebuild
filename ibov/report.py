# Write a self-contained HTML report (inline SVG charts, no external files or libraries)
# and a CSV of every line. Opens in any browser; can be emailed or attached as one file.

import csv
import html
import math
from datetime import date
from pathlib import Path

from ibov.calc import Analysis, Line

LEVEL_TOL_PCT = 0.01  # acceptance: rebuilt level within 0.01% of the official close
CONTRIB_TOL_PTS = 1.0  # contributions must add up to the official change within one index point


# Format a number of index points, or a dash if there is none
def pts(value: float | None, decimals: int = 2, sign: bool = False) -> str:
    if value is None:
        return "–"

    if sign:
        return f"{value:+,.{decimals}f}"
    else:
        return f"{value:,.{decimals}f}"


def escape(text: str) -> str:
    return html.escape(text)


# ---------------------------------------------------------------- charts
# Horizontal bar chart of one Line attribute. labels holds a (main, sub) pair per row.
def bar_chart(rows: list[Line], value_key: str, labels: list[tuple[str, str]], value_format: str,
              suffix: str = "", highlighted: list[Line] | None = None, left: int = 230,
              diverging: bool = False) -> str:
    width = 760
    bar_height = 20
    gap = 6
    right = 70

    values = []

    for row in rows:
        values.append(getattr(row, value_key))

    largest = 0

    for value in values:
        largest = max(largest, abs(value))

    if largest == 0:
        largest = 1

    plot_width = width - left - right

    if diverging:
        zero = left + plot_width / 2
        scale = (plot_width / 2) / largest
    else:
        zero = left
        scale = plot_width / largest

    height = len(rows) * (bar_height + gap) + 10

    out = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart">']

    if diverging:
        out.append(f'<line x1="{zero}" x2="{zero}" y1="0" y2="{height}" class="axis"/>')

    for index, row in enumerate(rows):
        value = values[index]
        main, sub = labels[index]
        shown = format(value, value_format) + suffix

        y = 4 + index * (bar_height + gap)
        bar_width = max(abs(value) * scale, 1)

        if value >= 0:
            x = zero
        else:
            x = zero + value * scale

        if diverging:
            if value >= 0:
                css_class = "up"
            else:
                css_class = "down"
        elif highlighted is None or row in highlighted:
            css_class = "accent"
        else:
            css_class = "muted"

        out.append(f'<text x="0" y="{y + 14}" class="lbl">{escape(main)}</text>')
        out.append(f'<text x="70" y="{y + 14}" class="sub">{escape(sub)}</text>')
        out.append(f'<rect x="{x:.1f}" y="{y}" width="{bar_width:.1f}" height="{bar_height}" rx="3" class="{css_class}">'
                   f'<title>{escape(main)} {escape(sub)}: {escape(shown)}</title></rect>')

        if diverging and value < 0:  # value label to the left of a negative bar
            out.append(f'<text x="{x - 6:.1f}" y="{y + 14}" class="val" text-anchor="end">{escape(shown)}</text>')
        else:
            out.append(f'<text x="{x + bar_width + 6:.1f}" y="{y + 14}" class="val">{escape(shown)}</text>')

    out.append("</svg>")

    return "".join(out)


# Line chart of the official closes, with one session marked
def line_chart(series: list[tuple[date, float]], mark_date: date) -> str:
    if len(series) < 2:
        return "<p class='note'>Not enough official closes stored yet to draw a history.</p>"

    width = 760
    height = 260
    left = 70
    right = 20
    top_margin = 16
    bottom = 30

    first_day = series[0][0].toordinal()
    last_day = series[-1][0].toordinal()

    levels = []

    for _, level in series:
        levels.append(level)

    padding = (max(levels) - min(levels)) * 0.08 or 1
    low = min(levels) - padding
    high = max(levels) + padding

    def x_position(ordinal: int) -> float:
        return left + (ordinal - first_day) / (last_day - first_day or 1) * (width - left - right)

    def y_position(level: float) -> float:
        return top_margin + (high - level) / (high - low) * (height - top_margin - bottom)

    out = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart">']

    # Gridlines at round values: 1, 2 or 5 x 10^n
    raw_step = (high - low) / 4
    magnitude = 10 ** math.floor(math.log10(raw_step))

    for multiple in (1, 2, 5, 10):
        step = multiple * magnitude

        if step >= raw_step:
            break

    gridline = math.ceil(low / step) * step

    while gridline <= high:
        y = y_position(gridline)
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{left - 8}" y="{y + 4:.1f}" class="sub" text-anchor="end">{gridline:,.0f}</text>')
        gridline += step

    # Month ticks
    months = set()

    for day, _ in series:
        months.add((day.year, day.month))

    for year, month in sorted(months):
        first_of_month = date(year, month, 1)
        ordinal = first_of_month.toordinal()

        if first_day <= ordinal <= last_day:
            out.append(f'<text x="{x_position(ordinal):.1f}" y="{height - 8}" class="sub" text-anchor="middle">'
                       f'{first_of_month:%b}</text>')

    # The series itself
    points = []

    for index, (day, level) in enumerate(series):
        if index == 0:
            command = "M"
        else:
            command = "L"

        points.append(f"{command}{x_position(day.toordinal()):.1f},{y_position(level):.1f}")

    out.append(f'<path d="{" ".join(points)}" class="series"/>')

    # The marked session
    for day, level in series:
        if day == mark_date:
            x = x_position(day.toordinal())
            y = y_position(level)
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" class="dot">'
                       f'<title>{day}: {level:,.2f}</title></circle>'
                       f'<text x="{x - 8:.1f}" y="{y - 10:.1f}" class="val" text-anchor="end">'
                       f'{day:%d %b}: {level:,.2f}</text>')

    out.append("</svg>")

    return "".join(out)


# ---------------------------------------------------------------- checks
# The acceptance checks, as (check, result, passed). passed=None means it could not be run.
def checks(analysis: Analysis) -> list[tuple[str, str, bool | None]]:
    results = []

    # 1. Rebuilt level against the official close
    if analysis.diff_pts is not None:
        relative = analysis.diff_pts / analysis.official * 100

        if analysis.same_day_portfolio:
            results.append((
                "Rebuilt level vs official close (same-day portfolio and prices)",
                f"{pts(analysis.rebuilt)} vs {pts(analysis.official)}: {pts(analysis.diff_pts, 4, True)} pts "
                f"({relative:+.5f}%), tolerance {LEVEL_TOL_PCT}%",
                abs(relative) < LEVEL_TOL_PCT,
            ))
        else:
            results.append((
                "Rebuilt level vs official close",
                f"{pts(analysis.diff_pts, 4, True)} pts ({relative:+.5f}%), but the portfolio is from "
                f"{analysis.portfolio_date}, so this is approximate",
                None,
            ))
    else:
        results.append(("Rebuilt level vs official close", "not run: no prices for this session yet", None))

    # 2. Cross-day gap explained by named payouts
    if analysis.divisor_pts is not None:
        gap_pct = analysis.divisor_pts / analysis.official_prev * 100
        named = analysis.divisor_pts - analysis.residual_pts
        passed = analysis.reconciled_payouts or abs(analysis.residual_pts) <= CONTRIB_TOL_PTS

        if len(analysis.adjusted) == 0:
            explained = "no payouts went ex"
        elif len(analysis.adjusted) == 1:
            explained = f"1 named payout explains {pts(named)} pts"
        else:
            explained = f"{len(analysis.adjusted)} named payouts explain {pts(named)} pts"

        results.append((
            f"Previous close ({analysis.prev_date}) priced with this session's portfolio",
            f"{pts(analysis.divisor_pts, 2, True)} pts ({gap_pct:+.3f}%) above the official close; "
            f"{explained}, residual {pts(analysis.residual_pts, 2, True)}",
            passed,
        ))
    else:
        results.append((
            "Previous close priced with this session's portfolio",
            "not run: needs this session's own portfolio and the previous session's prices",
            None,
        ))

    # 3. Contributions against the official change
    if analysis.contribution_total is not None and analysis.official_change is not None:
        gap = analysis.contribution_total - analysis.official_change
        result = (f"{pts(analysis.contribution_total, 2, True)} vs {pts(analysis.official_change, 2, True)} pts "
                  f"(difference {pts(gap, 4, True)})")

        if analysis.same_day_portfolio:
            results.append(("Contributions add up to the official day's change", result, abs(gap) <= CONTRIB_TOL_PTS))
        else:
            results.append((
                "Contributions add up to the official day's change",
                result + f"; approximate: payouts that went ex on {analysis.date} cannot be inferred "
                         f"without that session's portfolio",
                None,
            ))

    # 4. B3's own totals
    results.append((
        "B3 total: line quantities add up to the published total",
        f"{analysis.qty_total_lines:,} vs {analysis.qty_total_header:,}",
        analysis.qty_total_lines == analysis.qty_total_header,
    ))
    results.append((
        "B3 total: published weights add up to 100%",
        f"{analysis.b3_weight_total:.3f}% (weights are rounded to three decimals)",
        abs(analysis.b3_weight_total - 100) <= 0.05,
    ))

    return results


# ---------------------------------------------------------------- page
CSS = """
:root{--bg:#fcfcfb;--fg:#0b0b0b;--fg2:#52514e;--grid:#e6e5e0;--axis:#a3a29b;--card:#f4f3ef;
 --accent:#2a78d6;--muted:#c3c2b7;--up:#2a78d6;--down:#e34948;--ok:#0a7d0a;--warn:#a15c00}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#1a1a19;--fg:#f0efec;--fg2:#c3c2b7;--grid:#383835;
 --axis:#6b6a64;--card:#242422;--accent:#3987e5;--muted:#55544f;--up:#3987e5;--down:#e66767;--ok:#5cc85c;--warn:#f0a43a}}
:root[data-theme="dark"]{--bg:#1a1a19;--fg:#f0efec;--fg2:#c3c2b7;--grid:#383835;
 --axis:#6b6a64;--card:#242422;--accent:#3987e5;--muted:#55544f;--up:#3987e5;--down:#e66767;--ok:#5cc85c;--warn:#f0a43a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
 font:15px/1.5 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:860px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:40px 0 4px}
.lead{color:var(--fg2);margin:0 0 20px}.note{color:var(--fg2);font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}
.kpi{background:var(--card);border-radius:8px;padding:12px 14px}.kpi b{display:block;font-size:22px}
.kpi span{color:var(--fg2);font-size:13px}
.status{border-left:4px solid var(--ok);padding:8px 14px;background:var(--card);border-radius:4px}
.status.warn{border-color:var(--warn)}
svg.chart{width:100%;height:auto;margin-top:8px}svg text{font-size:12px;fill:var(--fg)}
svg .sub{fill:var(--fg2)}svg .val{fill:var(--fg)}svg .lbl{font-weight:600}
svg .accent{fill:var(--accent)}svg .muted{fill:var(--muted)}svg .up{fill:var(--up)}svg .down{fill:var(--down)}
svg .grid{stroke:var(--grid)}svg .axis{stroke:var(--axis)}svg .series{fill:none;stroke:var(--accent);stroke-width:2}
svg .dot{fill:var(--accent);stroke:var(--bg);stroke-width:2}
.tablewrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}
th,td{padding:5px 8px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
th:nth-child(-n+3),td:nth-child(-n+3){text-align:left}th{cursor:pointer;color:var(--fg2);font-weight:600}
code{font-size:13px}
table.checks td,table.checks th{text-align:left;white-space:normal;vertical-align:top}
.pass{color:var(--ok);font-weight:600}.fail{color:var(--down);font-weight:600}.na{color:var(--fg2)}
"""

SORT_JS = """
document.querySelectorAll('table.sortable th').forEach((th,i)=>th.addEventListener('click',()=>{
 const tb=th.closest('table').tBodies[0],rows=[...tb.rows],asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
 const v=r=>{const t=r.cells[i].dataset.v??r.cells[i].textContent;const n=parseFloat(t);return isNaN(n)?t:n};
 rows.sort((a,b)=>{const x=v(a),y=v(b);return (x>y?1:x<y?-1:0)*(asc?1:-1)});rows.forEach(r=>tb.appendChild(r))}));
"""


# A table cell that sorts by its raw value
def sortable_cell(value: float | int | None, value_format: str) -> str:
    if value is None:
        return "<td data-v=''>–</td>"

    return f"<td data-v='{value}'>{format(value, value_format)}</td>"


# Combined weight of the first `count` lines of a ranked list
def top_weight(ranked: list[Line], weight_key: str, count: int) -> float:
    weights = []

    for line in ranked[:count]:
        weights.append(getattr(line, weight_key) or 0)

    return sum(weights)


# Build the whole report page as one HTML string
def build_html(analysis: Analysis, history: list[tuple[date, float]], provenance: list[str],
               warnings: list[str]) -> str:
    session_date = analysis.date

    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Ibovespa rebuild {session_date}</title><style>{CSS}</style></head><body><main>"]
    parts.append(f"<h1>Ibovespa rebuilt from B3 data</h1><p class='lead'>Session of {session_date:%A %d %B %Y}. "
                 f"Every number below is computed from B3's public files; the official close is used only to check it.</p>")

    # Headline numbers
    if analysis.diff_pts is not None:
        difference = f"{pts(analysis.diff_pts, sign=True)} pts"
    else:
        difference = "–"

    if analysis.official_change is not None:
        day_change = f"{pts(analysis.official_change, sign=True)} pts"
    else:
        day_change = "–"

    headline = [
        ("Official close", pts(analysis.official)),
        ("Rebuilt", pts(analysis.rebuilt)),
        ("Difference", difference),
        ("Day change (official)", day_change),
        ("Lines in index", f"{len(analysis.lines)}"),
    ]

    parts.append("<div class='kpis'>")

    for title, value in headline:
        parts.append(f"<div class='kpi'><span>{escape(title)}</span><b>{escape(value)}</b></div>")

    parts.append("</div>")

    # Status line
    if analysis.diff_pts is not None:
        relative = analysis.diff_pts / analysis.official * 100

        if abs(relative) < 0.01:
            css_class, verdict = "status", "Reconciled"
        else:
            css_class, verdict = "status warn", "Check"

        parts.append(f"<p class='{css_class}'>{verdict}: rebuilt level is "
                     f"{pts(analysis.diff_pts, sign=True)} points ({relative:+.4f}%) from B3's official close.</p>")

    for warning in warnings:
        parts.append(f"<p class='status warn'>{escape(warning)}</p>")

    # Checks
    parts.append("<h2>Checks</h2><p class='note'>Each figure is recomputed from the raw files and set against "
                 "B3's own number.</p><div class='tablewrap'><table class='checks'><tr><th>Check</th>"
                 "<th>Result</th><th>Status</th></tr>")

    for name, result, passed in checks(analysis):
        if passed is None:
            tag = "<span class='na'>Not run</span>"
        elif passed:
            tag = "<span class='pass'>Pass</span>"
        else:
            tag = "<span class='fail'>Fail</span>"

        parts.append(f"<tr><td>{escape(name)}</td><td>{escape(result)}</td><td>{tag}</td></tr>")

    parts.append("</table></div>")

    # History
    parts.append("<h2>Index level, official closes</h2>"
                 "<p class='note'>From B3's index statistics. The marked point is this report's session.</p>")
    parts.append(line_chart(history, session_date))

    # Contributions
    contributing = []

    for line in analysis.lines:
        if line.contribution_pts is not None:
            contributing.append(line)

    if contributing:
        by_contribution = sorted(contributing, key=lambda line: -line.contribution_pts)

        gains = []
        losses = []

        for line in by_contribution[:10]:
            if line.contribution_pts > 0:
                gains.append(line)

        for line in by_contribution[::-1][:10]:
            if line.contribution_pts < 0:
                losses.append(line)

        rows = gains + losses[::-1]  # largest gain at the top, largest loss at the bottom

        labels = []

        for line in rows:
            labels.append((line.ticker, f"{line.name.title()}  {line.change_pct:+.2f}%"))

        parts.append(f"<h2>What moved the index on {session_date:%d %b}</h2>"
                     f"<p class='note'>Contribution = quantity &times; price change &divide; redutor, in index points. "
                     f"The ten largest gains and ten largest losses. All {len(contributing)} lines add up to "
                     f"{pts(analysis.contribution_total, sign=True)} points; the official change was "
                     f"{pts(analysis.official_change, sign=True)}.</p>")
        parts.append(bar_chart(rows, "contribution_pts", labels, "+,.1f", diverging=True, left=250))

    # Payouts
    if analysis.adjusted:
        if analysis.reconciled_payouts:
            conclusion = ", so the payouts below are scaled to match the divisor exactly."
        else:
            conclusion = (". The difference is too large to be rounding: a payout may have been missed, "
                          "or the basket changed (rebalance).")

        parts.append(f"<h2>Payouts absorbed by the divisor</h2><p class='note'>Lines whose previous close in B3's "
                     f"weights is lower than the traded close: the share went ex on {session_date:%d %b} and B3 lowered the "
                     f"redutor instead of letting the index fall. Payouts are implied from B3's weights "
                     f"(accurate to the third decimal of the weight), not taken from company announcements. "
                     f"B3's divisor removed {pts(analysis.divisor_pts)} index points; the lines detected one by one "
                     f"account for {pts(analysis.divisor_pts - analysis.residual_pts)} "
                     f"(difference {pts(analysis.residual_pts, sign=True)}, from the rounding of weights)"
                     f"{conclusion}</p>")
        parts.append("<div class='tablewrap'><table><tr><th>Line</th><th>Company</th><th>Type</th>"
                     "<th>Traded close</th><th>Adjusted close</th><th>Payout / share</th><th>Total (R$m)</th>"
                     "<th>Index pts</th></tr>")

        for line in analysis.adjusted:
            parts.append(f"<tr><td>{escape(line.ticker)}</td><td>{escape(line.name.title())}</td>"
                         f"<td>{escape(line.share_type)}</td>"
                         f"<td>{line.prev_close:.2f}</td><td>{line.prev_adj:.2f}</td><td>{line.payout:.3f}</td>"
                         f"<td>{line.payout * line.qty / 1e6:,.0f}</td>"
                         f"<td>{line.payout * line.qty / analysis.redutor:.1f}</td></tr>")

        parts.append("</table></div>")

    # Weights: ours if prices are in, otherwise B3's
    have_prices = False

    for line in analysis.lines:
        if line.weight_pct is not None:
            have_prices = True

    if have_prices:
        weight_key = "weight_pct"
        weight_source = "Computed from closing prices"
    else:
        weight_key = "b3_weight_pct"
        weight_source = "As published by B3"

    ranked = sorted(analysis.lines, key=lambda line: -(getattr(line, weight_key) or 0))

    parts.append(f"<h2>Weights</h2><p class='note'>{weight_source}. "
                 f"Top 5: {top_weight(ranked, weight_key, 5):.1f}% &middot; "
                 f"top 10: {top_weight(ranked, weight_key, 10):.1f}% &middot; "
                 f"top 20: {top_weight(ranked, weight_key, 20):.1f}% of the index.</p>")

    top_lines = ranked[:20]
    labels = []

    for line in top_lines:
        labels.append((line.ticker, line.name.title()))

    parts.append(bar_chart(top_lines, weight_key, labels, ".2f", suffix="%", highlighted=ranked[:10]))

    # Every line
    parts.append("<h2>All lines</h2><p class='note'>Click a column to sort.</p><div class='tablewrap'>"
                 "<table class='sortable'><thead><tr><th>Line</th><th>Company</th><th>Type</th><th>Quantity</th>"
                 "<th>Close</th><th>Change %</th><th>Weight %</th><th>B3 weight %</th><th>Contribution</th>"
                 "</tr></thead><tbody>")

    for line in ranked:
        parts.append(f"<tr><td>{escape(line.ticker)}</td><td>{escape(line.name.title())}</td>"
                     f"<td>{escape(line.share_type)}</td>"
                     + sortable_cell(line.qty, ",")
                     + sortable_cell(line.close, ".2f")
                     + sortable_cell(line.change_pct, "+.2f")
                     + sortable_cell(line.weight_pct, ".3f")
                     + sortable_cell(line.b3_weight_pct, ".3f")
                     + sortable_cell(line.contribution_pts, "+.2f")
                     + "</tr>")

    parts.append("</tbody></table></div>")

    # Method and provenance
    parts.append(f"<h2>Method and data</h2><p class='note'>Index = &Sigma; (close &times; theoretical quantity) "
                 f"&divide; redutor. Redutor for this session: {analysis.redutor:,.8f}. "
                 f"Previous session: {analysis.prev_date or '–'}.</p><ul class='note'>")

    for source in provenance:
        parts.append(f"<li>{escape(source)}</li>")

    parts.append("</ul>")
    parts.append(f"<script>{SORT_JS}</script></main></body></html>")

    return "".join(parts)


# Write one CSV row per line, heaviest B3 weight first
def write_csv(analysis: Analysis, path: Path) -> None:
    columns = ["ticker", "name", "share_type", "qty", "prev_close", "prev_adj", "payout", "close",
               "change_pct", "value", "weight_pct", "b3_weight_pct", "contribution_pts"]

    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(columns)

        for line in sorted(analysis.lines, key=lambda line: -line.b3_weight_pct):
            row = []

            for column in columns:
                row.append(getattr(line, column))

            writer.writerow(row)
