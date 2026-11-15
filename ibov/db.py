# Local SQLite store. Raw downloads are kept on disk untouched; this database is
# always rebuildable from them (python -m ibov ingest --full).

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from ibov import b3

SCHEMA = """
create table if not exists portfolio_header(
    portfolio_date text primary key, redutor real not null, total_qty integer not null,
    source_file text, ingested_at text);
create table if not exists portfolio_line(
    portfolio_date text, ticker text, name text, share_type text, qty integer, weight_pct real,
    primary key(portfolio_date, ticker));
create table if not exists quote(
    trade_date text, ticker text, bdi text, short_name text, spec text,
    open real, high real, low real, avg real, close real,
    trades integer, shares_traded integer, value_traded real, isin text,
    primary key(trade_date, ticker));
create table if not exists index_close(
    trade_date text primary key, close real not null, source_file text);
create table if not exists raw_file(path text primary key, stamp text, kind text, ingested_at text);
"""


# Open the database and create the tables if they don't exist yet
def connect(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)

    return connection


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- writes
def save_portfolio(connection: sqlite3.Connection, portfolio: b3.Portfolio, source: str) -> None:
    portfolio_date = portfolio.portfolio_date.isoformat()

    connection.execute(
        "insert or replace into portfolio_header values(?,?,?,?,?)",
        (portfolio_date, portfolio.redutor, portfolio.total_qty, source, now()),
    )
    connection.execute("delete from portfolio_line where portfolio_date=?", (portfolio_date,))

    rows = []

    for line in portfolio.lines:
        rows.append((portfolio_date, line["ticker"], line["name"], line["share_type"], line["qty"], line["weight_pct"]))

    connection.executemany("insert into portfolio_line values(?,?,?,?,?,?)", rows)


def save_quotes(connection: sqlite3.Connection, quotes: list[dict]) -> None:
    # Prefer the round-lot line (BDI 02) when B3 lists a ticker more than once
    quotes = sorted(quotes, key=lambda quote: quote["bdi"] != "02")

    seen = set()
    rows = []

    for quote in quotes:
        key = (quote["trade_date"], quote["ticker"])

        if key in seen:
            continue

        seen.add(key)
        rows.append((
            quote["trade_date"].isoformat(), quote["ticker"], quote["bdi"], quote["short_name"], quote["spec"],
            quote["open"], quote["high"], quote["low"], quote["avg"], quote["close"],
            quote["trades"], quote["shares_traded"], quote["value_traded"], quote["isin"],
        ))

    connection.executemany("insert or replace into quote values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def save_history(connection: sqlite3.Connection, closes: dict, source: str) -> None:
    rows = []

    for trade_date, close in closes.items():
        rows.append((trade_date.isoformat(), close, source))

    connection.executemany("insert or replace into index_close values(?,?,?)", rows)


# Load every raw file not yet ingested. Files are recognised by content, not name,
# so files saved by earlier versions of the tool are picked up too.
def ingest_dir(connection: sqlite3.Connection, raw_dir: Path) -> list[str]:
    done = {}

    for row in connection.execute("select path, stamp from raw_file"):
        done[row["path"]] = row["stamp"]

    log = []

    for file in sorted(raw_dir.rglob("*")):
        if not file.is_file():
            continue

        stats = file.stat()
        stamp = f"{stats.st_size}-{int(stats.st_mtime)}"

        if done.get(str(file)) == stamp:  # unchanged since last ingest
            continue

        kind = None

        if file.suffix.lower() == ".zip" and file.name.upper().startswith("COTAHIST"):
            quotes = b3.parse_cotahist(file.read_bytes())
            save_quotes(connection, quotes)
            kind = f"quotes ({len(quotes)} instruments)"

        elif file.suffix.lower() == ".json":
            data = json.loads(file.read_bytes())

            if b3.is_portfolio(data):
                portfolio = b3.parse_portfolio(file.read_bytes())
                save_portfolio(connection, portfolio, file.name)
                kind = f"portfolio {portfolio.portfolio_date}"

            elif b3.is_history(data):
                year = int(file.stem.split("_")[1])  # history_<year>.json
                closes = b3.parse_history(file.read_bytes(), year)
                save_history(connection, closes, file.name)
                kind = f"index history {year} ({len(closes)} closes)"

        if kind:
            connection.execute("insert or replace into raw_file values(?,?,?,?)", (str(file), stamp, kind, now()))
            log.append(f"{file.name}: {kind}")

    connection.commit()

    return log


# ---------------------------------------------------------------- reads
# The saved portfolio for one session, or None if it wasn't saved
def portfolio(connection: sqlite3.Connection, portfolio_date: date) -> b3.Portfolio | None:
    header = connection.execute(
        "select * from portfolio_header where portfolio_date=?", (portfolio_date.isoformat(),)
    ).fetchone()

    if not header:
        return None

    lines = []

    for row in connection.execute(
        "select ticker,name,share_type,qty,weight_pct from portfolio_line where portfolio_date=? "
        "order by weight_pct desc", (portfolio_date.isoformat(),)
    ):
        lines.append(dict(row))

    return b3.Portfolio(portfolio_date, header["redutor"], header["total_qty"], lines)


# Closing price of every instrument on one trading day
def closes(connection: sqlite3.Connection, trade_date: date) -> dict[str, float]:
    prices = {}

    for row in connection.execute("select ticker, close from quote where trade_date=?", (trade_date.isoformat(),)):
        prices[row["ticker"]] = row["close"]

    return prices


# B3's official close for one trading day, or None
def index_close(connection: sqlite3.Connection, trade_date: date) -> float | None:
    row = connection.execute("select close from index_close where trade_date=?", (trade_date.isoformat(),)).fetchone()

    if row:
        return row["close"]
    else:
        return None


# The last trading day before a date, from the official history, else from the quotes
def previous_session(connection: sqlite3.Connection, trade_date: date) -> date | None:
    queries = (
        "select max(trade_date) m from index_close where trade_date < ?",
        "select max(trade_date) m from quote where trade_date < ?",
    )

    for query in queries:
        row = connection.execute(query, (trade_date.isoformat(),)).fetchone()

        if row and row["m"]:
            return date.fromisoformat(row["m"])

    return None


# The raw file a stored row came from
def source_file(connection: sqlite3.Connection, table: str, column: str, row_date: date) -> str | None:
    row = connection.execute(f"select source_file from {table} where {column}=?", (row_date.isoformat(),)).fetchone()

    if row:
        return row["source_file"]
    else:
        return None


# Every official close stored, oldest first
def index_history(connection: sqlite3.Connection) -> list[tuple[date, float]]:
    history = []

    for row in connection.execute("select trade_date, close from index_close order by trade_date"):
        history.append((date.fromisoformat(row["trade_date"]), row["close"]))

    return history


# Every distinct date stored in a table column, oldest first
def dates(connection: sqlite3.Connection, table: str, column: str) -> list[date]:
    found = []

    for row in connection.execute(f"select distinct {column} from {table} order by 1"):
        found.append(date.fromisoformat(row[0]))

    return found
