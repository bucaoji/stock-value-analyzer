import argparse
import csv
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_value_analyzer.analysis import extract_annual_metrics, money


NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true"
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

EXCLUDED_NAME_FRAGMENTS = [
    "acquisition corp",
    "capital trust",
    "closed end fund",
    "debenture",
    "dep shr",
    "depositary",
    "depositary share",
    "etf",
    "exchange traded",
    "fixed rate",
    "fund",
    "notes due",
    "preferred",
    "pfd",
    "right",
    "rights",
    "spac",
    "unit",
    "units",
    "warrant",
    "warrants",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Screen U.S. stocks by market cap and five-year free cash flow.")
    parser.add_argument("--min-market-cap", type=float, default=100_000_000)
    parser.add_argument("--max-market-cap", type=float, default=1_000_000_000)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--user-agent", default=None)
    parser.add_argument("--sleep", type=float, default=0.12)
    args = parser.parse_args()

    user_agent = args.user_agent or __import__("os").environ.get(
        "STOCK_ANALYZER_USER_AGENT", "stock-value-analyzer/0.1 contact@example.com"
    )
    root = Path(args.data_dir)
    cache = root / "screen_cache"
    output = root / "screens"
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)

    rows = nasdaq_rows(cache / "nasdaq_screener.json")
    ticker_to_cik = sec_ticker_map(cache / "sec_company_tickers.json", user_agent)
    candidates = [
        row
        for row in rows
        if is_common_us_operating_company(row)
        and args.min_market_cap <= float(row["marketCap"]) <= args.max_market_cap
        and normalized_ticker(row["symbol"]) in ticker_to_cik
    ]
    candidates = deduplicate_by_cik(candidates, ticker_to_cik)

    results = []
    for index, row in enumerate(candidates, start=1):
        ticker = normalized_ticker(row["symbol"])
        cik = ticker_to_cik[ticker]["cik"]
        facts_path = cache / "companyfacts" / f"{cik}.json"
        try:
            facts = companyfacts(cik, facts_path, user_agent, args.sleep)
        except (HTTPError, URLError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[{index}/{len(candidates)}] skip {ticker}: {exc}")
            continue
        metrics = extract_annual_metrics(facts, years=5)
        fcf_values = [metric.free_cash_flow for metric in metrics if metric.free_cash_flow is not None]
        if len(fcf_values) < 5:
            continue
        result = {
            "symbol": row["symbol"],
            "name": clean_name(row["name"]),
            "sector": row.get("sector") or "",
            "industry": row.get("industry") or "",
            "market_cap": float(row["marketCap"]),
            "cik": cik,
            "years": ",".join(str(metric.fiscal_year) for metric in metrics),
            "fcf_years": len(fcf_values),
            "positive_fcf_years": sum(1 for value in fcf_values if value > 0),
            "cumulative_fcf": sum(fcf_values),
            "average_fcf": sum(fcf_values) / len(fcf_values),
            "latest_fcf": fcf_values[-1],
            "latest_revenue": next((metric.revenue for metric in reversed(metrics) if metric.revenue is not None), None),
            "currency": metrics[-1].currency if metrics else "USD",
        }
        results.append(result)
        if index % 50 == 0:
            print(f"[{index}/{len(candidates)}] screened; current qualified results: {len(results)}")

    results.sort(key=lambda item: (item["cumulative_fcf"], item["positive_fcf_years"], item["latest_fcf"]), reverse=True)
    top = results[: args.top]
    csv_path = output / "us_100m_1b_top_fcf.csv"
    md_path = output / "us_100m_1b_top_fcf.md"
    write_csv(csv_path, top)
    write_markdown(md_path, top, len(candidates), len(results), args)
    print(f"Candidates in market-cap range with CIK mapping: {len(candidates)}")
    print(f"Companies with usable FCF history: {len(results)}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {md_path}")
    return 0


def fetch_json(url: str, user_agent: str) -> Dict[str, object]:
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/json", "Accept-Encoding": "identity"})
    with urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def nasdaq_rows(path: Path) -> List[Dict[str, str]]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["data"]["rows"]
    request = Request(NASDAQ_SCREENER_URL, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urlopen(request, timeout=45) as response:
        data = json.loads(response.read().decode("utf-8"))
    path.write_text(json.dumps(data), encoding="utf-8")
    return data["data"]["rows"]


def sec_ticker_map(path: Path, user_agent: str) -> Dict[str, Dict[str, str]]:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = fetch_json(SEC_COMPANY_TICKERS_URL, user_agent)
        path.write_text(json.dumps(data), encoding="utf-8")
    mapping = {}
    for row in data.values():
        ticker = normalized_ticker(str(row.get("ticker", "")))
        if ticker:
            mapping[ticker] = {"cik": str(row["cik_str"]).zfill(10), "title": str(row.get("title", ""))}
    return mapping


def companyfacts(cik: str, path: Path, user_agent: str, sleep: float) -> Dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    time.sleep(sleep)
    data = fetch_json(SEC_COMPANYFACTS_URL.format(cik=cik), user_agent)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def is_common_us_operating_company(row: Dict[str, str]) -> bool:
    symbol = row.get("symbol", "")
    name = row.get("name", "")
    market_cap = row.get("marketCap", "")
    if row.get("country") != "United States":
        return False
    if not market_cap or float(market_cap or 0) <= 0:
        return False
    if any(mark in symbol for mark in ["^", "/"]) or symbol.endswith(("W", "R", "U")):
        return False
    lower_name = name.lower()
    return not any(fragment in lower_name for fragment in EXCLUDED_NAME_FRAGMENTS)


def deduplicate_by_cik(
    candidates: Iterable[Dict[str, str]],
    ticker_to_cik: Dict[str, Dict[str, str]],
) -> List[Dict[str, str]]:
    by_cik: Dict[str, Dict[str, str]] = {}
    for row in candidates:
        cik = ticker_to_cik[normalized_ticker(row["symbol"])]["cik"]
        current = by_cik.get(cik)
        if current is None or float(row["marketCap"]) > float(current["marketCap"]):
            by_cik[cik] = row
    return list(by_cik.values())


def normalized_ticker(symbol: str) -> str:
    return symbol.upper().replace(".", "-").strip()


def clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("Common Stock", "").replace("Class A", "Class A ")).strip()


def write_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    fields = [
        "rank",
        "symbol",
        "name",
        "sector",
        "industry",
        "market_cap",
        "cik",
        "years",
        "fcf_years",
        "positive_fcf_years",
        "cumulative_fcf",
        "average_fcf",
        "latest_fcf",
        "latest_revenue",
        "currency",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for rank, row in enumerate(rows, start=1):
            writer.writerow({"rank": rank, **row})


def write_markdown(path: Path, rows: List[Dict[str, object]], candidate_count: int, result_count: int, args) -> None:
    lines = [
        "# U.S. $100M-$1B Market Cap: Top Free Cash Flow Screen",
        "",
        f"- Generated: {date.today().isoformat()}",
        "- Universe: Nasdaq screener rows with country = United States, market cap between $100M and $1B, mapped to SEC CIK.",
        "- Exclusions: preferreds, warrants, rights, units, funds/ETFs, most SPAC/acquisition vehicles, and unmapped tickers.",
        "- Ranking: cumulative free cash flow over the latest available five fiscal years from SEC XBRL facts.",
        f"- Candidates with CIK mapping: {candidate_count}",
        f"- Companies with usable FCF history: {result_count}",
        "",
        "| Rank | Ticker | Company | Sector | Market Cap | FCF Years | Positive Years | 5Y Cumulative FCF | Avg FCF | Latest FCF |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank, row in enumerate(rows, start=1):
        currency = str(row["currency"])
        lines.append(
            f"| {rank} | {row['symbol']} | {row['name']} | {row['sector']} | "
            f"${row['market_cap'] / 1_000_000:.1f}M | {row['fcf_years']} | {row['positive_fcf_years']} | "
            f"{money(float(row['cumulative_fcf']), currency)} | {money(float(row['average_fcf']), currency)} | "
            f"{money(float(row['latest_fcf']), currency)} |"
        )
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- This is a screen, not a full company analysis report.",
            "- Financial companies, insurers, REIT-like businesses, and working-capital-heavy businesses may have less comparable operating cash flow.",
            "- SEC XBRL tag quality varies; top candidates should still go through the full one-company reviewed analysis before investment work.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
