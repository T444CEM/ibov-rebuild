# The index arithmetic. No file or network access, so everything here is testable.
#
#   line value   = close x theoretical quantity
#   index level  = sum of line values / redutor
#   weight       = line value / sum of line values
#   contribution = quantity x (close today - adjusted close yesterday) / redutor   (index points)
#
# The "adjusted close yesterday" matters on ex-dividend days. When a share goes ex, its price
# drops by the payout; B3 lowers the redutor so the index does not fall. B3's published weights
# for day t are computed on yesterday's closes AFTER that adjustment, so we can recover the
# adjusted price of every line from them and detect which lines were adjusted, and by how much.

from dataclasses import dataclass, field
from datetime import date

from ibov.b3 import Portfolio

WEIGHT_DECIMALS = 3  # B3 publishes weights to three decimals


@dataclass
class Line:
    ticker: str
    name: str
    share_type: str
    qty: int
    b3_weight_pct: float
    close: float | None = None  # day t
    prev_close: float | None = None  # day t-1, as traded
    prev_adj: float | None = None  # day t-1, adjusted for any payout going ex on day t
    payout: float = 0.0  # implied payout per share (R$), 0 if none detected
    value: float | None = None
    weight_pct: float | None = None
    change_pct: float | None = None
    contribution_pts: float | None = None


@dataclass
class Analysis:
    date: date
    prev_date: date | None
    redutor: float
    lines: list[Line]
    official: float | None = None
    official_prev: float | None = None
    rebuilt: float | None = None
    missing_today: list[str] = field(default_factory=list)
    missing_prev: list[str] = field(default_factory=list)
    adjustment_pts: float | None = None  # index points removed by payouts going ex today
    divisor_pts: float | None = None  # what the divisor change removed, from B3's own figures
    residual_pts: float | None = None  # divisor_pts minus the payouts detected line by line
    reconciled_payouts: bool = False  # True if payouts were scaled to match divisor_pts exactly
    portfolio_date: date | None = None  # session the portfolio belongs to (may differ from date)
    qty_total_header: int | None = None  # B3's published total theoretical quantity
    qty_total_lines: int | None = None  # the same total, summed line by line
    b3_weight_total: float | None = None  # B3's published weights, summed

    # Rebuilt level minus the official close
    @property
    def diff_pts(self) -> float | None:
        if self.rebuilt is None or self.official is None:
            return None

        return self.rebuilt - self.official

    # The official change from the previous session
    @property
    def official_change(self) -> float | None:
        if self.official is None or self.official_prev is None:
            return None

        return self.official - self.official_prev

    # Sum of the contributions of every line that has one
    @property
    def contribution_total(self) -> float | None:
        contributions = []

        for line in self.lines:
            if line.contribution_pts is not None:
                contributions.append(line.contribution_pts)

        if contributions:
            return sum(contributions)
        else:
            return None

    @property
    def same_day_portfolio(self) -> bool:
        return self.portfolio_date == self.date

    # Lines that went ex-dividend this session
    @property
    def adjusted(self) -> list[Line]:
        adjusted_lines = []

        for line in self.lines:
            if line.payout > 0:
                adjusted_lines.append(line)

        return adjusted_lines


# For each line, return (adjusted previous close, payout) implied by B3's weights.
#
# B3's weight_i = q_i x adj_price_i / (official_prev x redutor). Weights are rounded to
# WEIGHT_DECIMALS, so the implied price is only known to +/- half a unit of the last decimal.
# A line counts as adjusted only if the traded close exceeds the implied price by more than
# `sigmas` times that rounding error (and by at least `min_rs` reais).
def detect_adjustments(portfolio: Portfolio, prev_close: dict, official_prev: float,
                       sigmas: float = 3.0, min_rs: float = 0.005) -> dict[str, tuple[float, float]]:
    value_adjusted = official_prev * portfolio.redutor
    half_unit = 0.5 * 10 ** -WEIGHT_DECIMALS / 100

    adjustments = {}

    for line in portfolio.lines:
        traded = prev_close.get(line["ticker"])

        if traded is None:
            continue

        implied = line["weight_pct"] / 100 * value_adjusted / line["qty"]
        tolerance = half_unit * value_adjusted / line["qty"]
        gap = traded - implied

        if gap > max(sigmas * tolerance, min_rs):
            adjustments[line["ticker"]] = (implied, gap)
        else:
            adjustments[line["ticker"]] = (traded, 0.0)

    return adjustments


# Rebuild one session: level, weights, payouts and contributions
def analyse(portfolio: Portfolio, close_today: dict | None, close_prev: dict | None,
            official: float | None, official_prev: float | None,
            session_date: date | None = None, prev_date: date | None = None) -> Analysis:
    lines = []

    for row in portfolio.lines:
        lines.append(Line(row["ticker"], row["name"], row["share_type"], row["qty"], row["weight_pct"]))

    quantities = []
    weights = []

    for line in lines:
        quantities.append(line.qty)
        weights.append(line.b3_weight_pct)

    analysis = Analysis(
        session_date or portfolio.portfolio_date, prev_date, portfolio.redutor, lines, official, official_prev,
        portfolio_date=portfolio.portfolio_date,
        qty_total_header=portfolio.total_qty,
        qty_total_lines=sum(quantities),
        b3_weight_total=sum(weights),
    )
    redutor = portfolio.redutor

    if close_prev:
        for line in lines:
            if line.ticker not in close_prev:
                analysis.missing_prev.append(line.ticker)

        # B3's weights and redutor describe the move from the previous session INTO the portfolio's own
        # session, so payouts can only be read from them when the portfolio is this session's.
        detect = official_prev and analysis.same_day_portfolio

        if detect:
            adjustments = detect_adjustments(portfolio, close_prev, official_prev)
        else:
            adjustments = {}

        for line in lines:
            line.prev_close = close_prev.get(line.ticker)
            line.prev_adj, line.payout = adjustments.get(line.ticker, (line.prev_close, 0.0))

        if detect and not analysis.missing_prev:
            reconcile_payouts(analysis, official_prev)

        payout_values = []

        for line in lines:
            payout_values.append(line.qty * line.payout)

        analysis.adjustment_pts = sum(payout_values) / redutor

    if close_today:
        for line in lines:
            if line.ticker not in close_today:
                analysis.missing_today.append(line.ticker)

        values = []

        for line in lines:
            line.close = close_today.get(line.ticker)

            if line.close is not None:
                line.value = line.close * line.qty
                values.append(line.value)

        total = sum(values)

        if not analysis.missing_today:
            analysis.rebuilt = total / redutor

        for line in lines:
            if line.value is not None:
                line.weight_pct = 100 * line.value / total

            if line.close is not None and line.prev_adj:
                line.change_pct = 100 * (line.close / line.prev_adj - 1)
                line.contribution_pts = line.qty * (line.close - line.prev_adj) / redutor

    return analysis


# Exact anchor: B3's adjusted portfolio value is official_prev x today's redutor, so the
# payouts must add up to (traded value yesterday - that). Individual payouts carry the
# rounding error of the weights; the total does not, so they are scaled to match it.
def reconcile_payouts(analysis: Analysis, official_prev: float) -> None:
    redutor = analysis.redutor

    traded_values = []
    payout_values = []
    adjusted_count = 0

    for line in analysis.lines:
        traded_values.append(line.qty * line.prev_close)
        payout_values.append(line.qty * line.payout)

        if line.payout > 0:
            adjusted_count += 1

    needed = sum(traded_values) - official_prev * redutor
    found = sum(payout_values)

    analysis.divisor_pts = needed / redutor
    analysis.residual_pts = (needed - found) / redutor

    if found > 0 and abs(analysis.residual_pts) <= max(1.0, 1.0 * adjusted_count):
        scale = needed / found

        for line in analysis.lines:
            if line.payout > 0:
                line.payout *= scale
                line.prev_adj = line.prev_close - line.payout

        analysis.reconciled_payouts = True
