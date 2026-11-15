# Command line.
#
#   python -m ibov update            download today's portfolio, the index history and the latest quotes
#   python -m ibov report [DATE]     write the HTML report and CSV for a session (default: latest)
#   python -m ibov backfill FROM TO  download daily quote files for a date range
#   python -m ibov ingest            reload every raw file into the database
#   python -m ibov status            show what is stored

import argparse
import sqlite3
import urllib.error
import webbrowser
from datetime import date, timedelta
from pathlib import Path

from ibov import b3, db
from ibov.calc import analyse
from ibov.report import build_html, checks, write_csv


# Where the raw files, the database and the reports live
class Paths:
    def __init__(self, root: Path):
        self.root = root
        self.raw = root / "raw"
        self.db = root / "ibov.db"
        self.reports = root / "reports"

        for folder in (self.raw / "portfolio", self.raw / "quotes", self.raw / "history", self.reports):
            folder.mkdir(parents=True, exist_ok=True)


def quotes_file(paths: Paths, trade_date: date) -> Path:
    return paths.raw / "quotes" / f"COTAHIST_D{trade_date:%d%m%Y}.ZIP"


# Download one day's quotes file unless it is already saved
def download_quotes(paths: Paths, trade_date: date) -> bool:
    file = quotes_file(paths, trade_date)

    if file.exists():
        return True

    try:
        file.write_bytes(b3.http_get(b3.cotahist_url(trade_date)))
        print(f"  quotes {trade_date}: downloaded")
        return True
    except urllib.error.HTTPError as err:
        print(f"  quotes {trade_date}: not available (HTTP {err.code})")
        return False


def cmd_update(paths: Paths, connection: sqlite3.Connection, args: argparse.Namespace) -> int:
    print("Portfolio (B3 serves only the current one, so it is saved every run):")
    raw = b3.http_get(b3.portfolio_url())
    portfolio = b3.parse_portfolio(raw)

    (paths.raw / "portfolio" / f"IBOV_{portfolio.portfolio_date}.json").write_bytes(raw)
    print(f"  portfolio for {portfolio.portfolio_date}: {len(portfolio.lines)} lines, redutor {portfolio.redutor:,.8f}")

    for problem in portfolio.check():
        print(f"  WARNING: {problem}")

    today = date.today()
    years = [today.year]

    if today.month == 1:  # early January still needs last year's closes
        years.insert(0, today.year - 1)

    print("Official index history:")

    for year in years:
        (paths.raw / "history" / f"history_{year}.json").write_bytes(b3.http_get(b3.history_url(year)))
        print(f"  {year}: downloaded")

    db.ingest_dir(connection, paths.raw)

    print("Daily quotes:")
    sessions = []

    for session_date, _ in db.index_history(connection)[-2:]:
        sessions.append(session_date)

    if not sessions:  # no history: fall back to the last weekdays
        for days_back in range(1, 6):
            day = today - timedelta(days=days_back)

            if day.weekday() < 5 and len(sessions) < 2:
                sessions.append(day)

    for session_date in sessions:
        download_quotes(paths, session_date)

    if portfolio.portfolio_date > sessions[-1]:
        print(f"  (quotes for {portfolio.portfolio_date} are published after that session closes)")

    for entry in db.ingest_dir(connection, paths.raw):
        print(f"  stored {entry}")

    print("Done. Next: python -m ibov report")

    return 0


def cmd_backfill(paths: Paths, connection: sqlite3.Connection, args: argparse.Namespace) -> int:
    day = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    while day <= end:
        if day.weekday() < 5:  # weekdays only
            download_quotes(paths, day)

        day += timedelta(days=1)

    print_ingest_log(db.ingest_dir(connection, paths.raw))

    return 0


def cmd_ingest(paths: Paths, connection: sqlite3.Connection, args: argparse.Namespace) -> int:
    if args.full:
        connection.execute("delete from raw_file")

    print_ingest_log(db.ingest_dir(connection, paths.raw))

    return 0


def print_ingest_log(log: list[str]) -> None:
    if log:
        print("\n".join(log))
    else:
        print("nothing new")


def cmd_status(paths: Paths, connection: sqlite3.Connection, args: argparse.Namespace) -> int:
    db.ingest_dir(connection, paths.raw)

    portfolio_dates = db.dates(connection, "portfolio_header", "portfolio_date")
    quote_dates = db.dates(connection, "quote", "trade_date")
    history = db.index_history(connection)

    print(f"Portfolios saved: {join_dates(portfolio_dates)}")
    print(f"Quote files:      {join_dates(quote_dates)}")

    if history:
        print(f"Official closes:  {len(history)} ({history[0][0]} to {history[-1][0]})")
    else:
        print("Official closes:  0")

    return 0


def join_dates(dates: list[date]) -> str:
    if not dates:
        return "none"

    return ", ".join(str(day) for day in dates)


# The session's own portfolio, or the nearest saved one with a warning
def choose_portfolio(connection: sqlite3.Connection, session_date: date) -> tuple[b3.Portfolio | None, str | None]:
    portfolio = db.portfolio(connection, session_date)

    if portfolio:
        return portfolio, None

    saved = db.dates(connection, "portfolio_header", "portfolio_date")

    if not saved:
        return None, None

    # Nearest date first; on a tie, the later one
    nearest = min(saved, key=lambda day: (abs((day - session_date).days), day < session_date))
    warning = (f"No portfolio was saved for {session_date}; using the one for {nearest}. Its quantities and "
               f"redutor may differ (payouts, rebalances), so the level check is approximate.")

    return db.portfolio(connection, nearest), warning


# The latest session that has both a saved portfolio and quotes
def latest_session(connection: sqlite3.Connection) -> date | None:
    portfolio_dates = db.dates(connection, "portfolio_header", "portfolio_date")
    quote_dates = db.dates(connection, "quote", "trade_date")

    complete = []

    for day in portfolio_dates:
        if day in quote_dates:
            complete.append(day)

    for candidates in (complete, portfolio_dates, quote_dates):
        if candidates:
            return candidates[-1]

    return None


def cmd_report(paths: Paths, connection: sqlite3.Connection, args: argparse.Namespace) -> int:
    db.ingest_dir(connection, paths.raw)

    if args.date:
        session_date = date.fromisoformat(args.date)
    else:
        session_date = latest_session(connection)

    if session_date is None:
        print("Nothing stored yet. Run: python -m ibov update")
        return 1

    portfolio, warning = choose_portfolio(connection, session_date)

    if portfolio is None:
        print("No portfolio saved yet. Run: python -m ibov update")
        return 1

    warnings = []

    if warning:
        warnings.append(warning)

    prev_date = db.previous_session(connection, session_date)
    today_closes = db.closes(connection, session_date)

    if prev_date:
        prev_closes = db.closes(connection, prev_date)
        official_prev = db.index_close(connection, prev_date)
    else:
        prev_closes = {}
        official_prev = None

    if not today_closes:
        warnings.append(f"No quotes stored for {session_date} yet (B3 publishes them after the close). "
                        f"Showing B3's weights and the payout check against {prev_date}.")

    analysis = analyse(
        portfolio, today_closes or None, prev_closes or None,
        db.index_close(connection, session_date), official_prev,
        session_date=session_date, prev_date=prev_date,
    )

    if analysis.missing_today:
        warnings.append(f"No close for {', '.join(analysis.missing_today)} on {session_date}: level not rebuilt.")

    if not analysis.same_day_portfolio and prev_closes:
        warnings.append("Payouts are not inferred: B3's weights and redutor describe the move into the portfolio's "
                        "own session, so contributions use traded previous closes and miss any payout that went ex.")

    provenance = build_provenance(paths, connection, portfolio, session_date, prev_date)

    html_path = paths.reports / f"ibov_{session_date}.html"
    html_path.write_text(build_html(analysis, db.index_history(connection), provenance, warnings), encoding="utf-8")
    write_csv(analysis, paths.reports / f"ibov_{session_date}.csv")

    print(f"Session {session_date}: official {analysis.official}, rebuilt {analysis.rebuilt}, "
          f"difference {analysis.diff_pts}")

    if analysis.adjusted:
        payouts = []

        for line in analysis.adjusted:
            payouts.append(f"{line.ticker} R${line.payout:.3f}")

        print("Payouts absorbed by the divisor: " + ", ".join(payouts))

    for name, result, passed in checks(analysis):
        if passed is None:
            status = " -- "
        elif passed:
            status = "PASS"
        else:
            status = "FAIL"

        print(f"  [{status}] {name}: {result}")

    print(f"Report: {html_path}")

    if args.open:
        webbrowser.open(html_path.resolve().as_uri())

    return 0


# The raw file behind every number in the report
def build_provenance(paths: Paths, connection: sqlite3.Connection, portfolio: b3.Portfolio,
                     session_date: date, prev_date: date | None) -> list[str]:
    portfolio_file = db.source_file(connection, "portfolio_header", "portfolio_date", portfolio.portfolio_date)

    provenance = [
        f"Theoretical portfolio (quantities, redutor, B3 weights): raw/portfolio/{portfolio_file} "
        f"from B3's index API (GetPortfolioDay, IBOV), session {portfolio.portfolio_date}."
    ]

    for day in (session_date, prev_date):
        if day and quotes_file(paths, day).exists():
            provenance.append(f"Closing prices for {day}: raw/quotes/{quotes_file(paths, day).name}, "
                              f"B3 COTAHIST daily file (cash market, last trade = official close).")

    for day in (session_date, prev_date):
        if day and db.index_close(connection, day) is not None:
            history_file = db.source_file(connection, "index_close", "trade_date", day)
            provenance.append(f"Official close for {day}: raw/history/{history_file}, B3 index statistics (IBOV).")

    provenance.append(f"Database: {paths.db.name}, rebuildable from the raw files with: python -m ibov ingest --full. "
                      f"Raw files are kept unchanged in {paths.raw}.")

    return provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ibov", description="Rebuild the Ibovespa from B3's public data.")
    parser.add_argument("--data", default="data", help="folder for raw files, database and reports (default: ./data)")

    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("update", help="download today's portfolio, the index history and the latest quotes")

    report_parser = commands.add_parser("report", help="write the HTML report and CSV for a session")
    report_parser.add_argument("date", nargs="?", help="YYYY-MM-DD (default: latest)")
    report_parser.add_argument("--open", action="store_true", help="open the report in your browser")

    backfill_parser = commands.add_parser("backfill", help="download daily quote files for a date range")
    backfill_parser.add_argument("start")
    backfill_parser.add_argument("end")

    ingest_parser = commands.add_parser("ingest", help="reload raw files into the database")
    ingest_parser.add_argument("--full", action="store_true", help="reload everything, not just new files")

    commands.add_parser("status", help="show what is stored")

    args = parser.parse_args(argv)

    handlers = {
        "update": cmd_update,
        "report": cmd_report,
        "backfill": cmd_backfill,
        "ingest": cmd_ingest,
        "status": cmd_status,
    }

    paths = Paths(Path(args.data))
    connection = db.connect(paths.db)

    try:
        return handlers[args.command](paths, connection, args)
    finally:
        connection.close()  # an open handle locks ibov.db on Windows


if __name__ == "__main__":
    raise SystemExit(main())
