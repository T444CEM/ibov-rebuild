# Download and parse B3's public files.
#
# Three sources, all free and unauthenticated:
#   portfolio - the index's theoretical portfolio for the current session: one line per share class
#               (ticker, theoretical quantity, weight) plus the divisor ("redutor").
#               B3 serves only the CURRENT portfolio, so it must be saved every day.
#   quotes    - COTAHIST, B3's daily historical-series file: one fixed-width line per instrument
#               per trading day (open, high, low, close, volume, ISIN).
#   history   - the official daily closes of the index, one request per calendar year.

import base64
import io
import json
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime

USER_AGENT = "Mozilla/5.0 (ibov-rebuild)"
LISTADOS_URL = "https://sistemaswebb3-listados.b3.com.br"


# B3's endpoints take their query as base64-encoded JSON in the URL
def encode_query(query: dict) -> str:
    query_json = json.dumps(query, separators=(",", ":"))

    return base64.b64encode(query_json.encode()).decode()


# URL of the current theoretical portfolio of an index
def portfolio_url(index: str = "IBOV") -> str:
    query = {"language": "pt-br", "pageNumber": 1, "pageSize": 120, "index": index, "segment": "1"}

    return f"{LISTADOS_URL}/indexProxy/indexCall/GetPortfolioDay/{encode_query(query)}"


# URL of the official daily closes of an index for one calendar year
def history_url(year: int, index: str = "IBOV") -> str:
    query = {"index": index, "language": "pt-br", "year": str(year)}

    return f"{LISTADOS_URL}/indexStatisticsProxy/IndexCall/GetPortfolioDay/{encode_query(query)}"


# URL of the COTAHIST daily quotes file for one trading day
def cotahist_url(trade_date: date) -> str:
    return f"https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_D{trade_date:%d%m%Y}.ZIP"


# Download a URL and return the raw bytes
def http_get(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


# B3 writes numbers the Brazilian way: 13.980.694,43 means 13980694.43
def br_number(text) -> float:
    cleaned = str(text).strip().replace(".", "").replace(",", ".")

    return float(cleaned)


# ---------------------------------------------------------------- portfolio
@dataclass
class Portfolio:
    portfolio_date: date
    redutor: float  # the divisor: index points = portfolio value in R$ / redutor
    total_qty: int
    lines: list[dict] = field(default_factory=list)

    # Check the lines against B3's own header totals
    def check(self) -> list[str]:
        problems = []

        quantities = []
        weights = []

        for line in self.lines:
            quantities.append(line["qty"])
            weights.append(line["weight_pct"])

        qty_sum = sum(quantities)
        weight_sum = sum(weights)

        if qty_sum != self.total_qty:
            problems.append(f"quantities sum to {qty_sum:,}, header says {self.total_qty:,}")

        if abs(weight_sum - 100) > 0.05:
            problems.append(f"weights sum to {weight_sum:.3f}%")

        return problems


# Parse the portfolio JSON into a Portfolio
def parse_portfolio(raw: bytes) -> Portfolio:
    data = json.loads(raw)
    header = data["header"]

    lines = []

    for row in data["results"]:
        lines.append({
            "ticker": row["cod"].strip(),
            "name": row["asset"].strip(),
            "share_type": " ".join(row["type"].split()),  # B3 pads the type with runs of spaces
            "qty": int(round(br_number(row["theoricalQty"]))),
            "weight_pct": br_number(row["part"]),
        })

    return Portfolio(
        portfolio_date=datetime.strptime(header["date"], "%d/%m/%y").date(),
        redutor=br_number(header["reductor"]),
        total_qty=int(round(br_number(header["theoricalQty"]))),
        lines=lines,
    )


# Recognise a portfolio file by its content
def is_portfolio(data: dict) -> bool:
    if not isinstance(data, dict):
        return False

    return "header" in data and "reductor" in data.get("header", {})


# ---------------------------------------------------------------- index history
# Rows are days 1-31; columns rateValue1..rateValue12 are months. An empty cell means no session.
def parse_history(raw: bytes, year: int) -> dict[date, float]:
    closes = {}

    for row in json.loads(raw)["results"]:
        for month in range(1, 13):
            value = row.get(f"rateValue{month}")

            if value in (None, "", "-"):
                continue

            try:
                closes[date(year, month, int(row["day"]))] = br_number(value)
            except ValueError:  # e.g. 31 February never has a value, but be safe
                pass

    return closes


# Recognise an index history file by its content
def is_history(data: dict) -> bool:
    if not isinstance(data, dict):
        return False

    results = data.get("results")

    if not results or not isinstance(results, list):
        return False

    return "rateValue1" in results[0]


# ---------------------------------------------------------------- COTAHIST
# Field positions from B3's "SeriesHistoricas_Layout" document (1-based there, 0-based here)
COTAHIST_FIELDS = {
    "trade_date": (2, 10), "bdi": (10, 12), "ticker": (12, 24), "market_type": (24, 27),
    "short_name": (27, 39), "spec": (39, 49),
    "open": (56, 69), "high": (69, 82), "low": (82, 95), "avg": (95, 108), "close": (108, 121),
    "trades": (147, 152), "shares_traded": (152, 170), "value_traded": (170, 188),
    "isin": (230, 242),
}
TEXT_FIELDS = ("bdi", "ticker", "market_type", "short_name", "spec", "isin")
PRICE_FIELDS = ("open", "high", "low", "avg", "close", "value_traded")  # integers with 2 implied decimals


# Return one dict per cash-market (TPMERC 010) instrument.
# Record type 01 only: 00 is the header line and 99 the trailer.
def parse_cotahist(zip_bytes: bytes, market_type: str = "010") -> list[dict]:
    archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    text = archive.read(archive.namelist()[0]).decode("latin-1")

    rows = []

    for line in text.splitlines():
        if line[:2] != "01" or line[24:27] != market_type:
            continue

        row = {}

        for name, (start, end) in COTAHIST_FIELDS.items():
            row[name] = line[start:end]

        row["trade_date"] = datetime.strptime(row["trade_date"], "%Y%m%d").date()

        for name in TEXT_FIELDS:
            row[name] = row[name].strip()

        for name in PRICE_FIELDS:
            row[name] = int(row[name]) / 100

        row["trades"] = int(row["trades"])
        row["shares_traded"] = int(row["shares_traded"])

        rows.append(row)

    return rows
