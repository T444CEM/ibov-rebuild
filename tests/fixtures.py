# Build B3-format files from Python values, so the tests use the same parsers as real data

import io
import json
import zipfile
from datetime import date


# Write a number the Brazilian way: 1234.5 -> "1.234,50"
def br(value: float, decimals: int = 2) -> str:
    text = f"{value:,.{decimals}f}"

    return text.replace(",", "X").replace(".", ",").replace("X", ".")


# One 245-character COTAHIST record for a cash-market instrument
def cotahist_line(trade_date: date, ticker: str, close: float, bdi: str = "02") -> str:
    price = str(int(round(close * 100))).zfill(13)
    zero_price = "0" * 13

    line = (
        "01" + f"{trade_date:%Y%m%d}" + bdi + ticker.ljust(12) + "010" + ticker[:4].ljust(12) + "ON".ljust(10)
        + "   " + "R$  " + price * 5 + zero_price * 2 + "00010" + "0" * 18 + "0" * 18 + "0" * 13 + "0"
        + "99991231" + "0000001" + "0" * 13 + f"BR{ticker[:4]}ACNOR0" + "100"
    )

    assert len(line) == 245, len(line)

    return line


# A zipped COTAHIST file with a header, one record per close and a trailer
def cotahist_zip(trade_date: date, closes: dict) -> bytes:
    lines = ["00COTAHIST.2026BOVESPA 20261111".ljust(245)]

    for ticker, close in closes.items():
        lines.append(cotahist_line(trade_date, ticker, close))

    lines.append("99COTAHIST".ljust(245))
    body = "\n".join(lines) + "\n"

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"COTAHIST_D{trade_date:%d%m%Y}.TXT", body.encode("latin-1"))

    return buffer.getvalue()


# A portfolio file as B3's index API serves it
def portfolio_json(portfolio_date: date, redutor: float, lines: list[dict]) -> bytes:
    total_qty = 0
    results = []

    for line in lines:
        total_qty += line["qty"]
        results.append({
            "cod": line["ticker"],
            "asset": line["name"],
            "type": line.get("type", "ON NM"),
            "part": br(line["weight"], 3),
            "theoricalQty": br(line["qty"], 0),
        })

    header = {"date": f"{portfolio_date:%d/%m/%y}", "reductor": br(redutor, 8), "theoricalQty": br(total_qty, 0)}

    return json.dumps({"header": header, "results": results}).encode()


# An index history file: one row per day of the month, one column per month
def history_json(closes: dict) -> bytes:
    rows = []

    for day in range(1, 32):
        row = {"day": day}

        for month in range(1, 13):
            row[f"rateValue{month}"] = None

            for close_date, close in closes.items():
                if close_date.month == month and close_date.day == day:
                    row[f"rateValue{month}"] = br(close)

        rows.append(row)

    return json.dumps({"min": None, "max": None, "results": rows}).encode()
