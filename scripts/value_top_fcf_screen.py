import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_value_analyzer.analysis import extract_annual_metrics, money, percent
from stock_value_analyzer.valuation import ValuationAssumptions, value_from_metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Value the top companies from the U.S. FCF screen.")
    parser.add_argument("--input", default="data/screens/us_100m_1b_top_fcf.csv")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-prefix", default="data/screens/us_100m_1b_top_fcf_valuation")
    args = parser.parse_args()

    rows = read_rows(Path(args.input))
    cache = Path(args.data_dir) / "screen_cache" / "companyfacts"
    assumptions = ValuationAssumptions()
    valued = []
    for row in rows:
        facts_path = cache / f"{row['cik']}.json"
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        metrics = extract_annual_metrics(facts, years=5)
        valuation = value_from_metrics(metrics, float(row["market_cap"]), assumptions=assumptions)
        adjusted_score = adjusted_total_score(valuation.total_score, row)
        valued.append(
            {
                **row,
                "metrics": metrics,
                "valuation": valuation,
                "screen_notes": screen_notes(row),
                "adjusted_total_score": adjusted_score,
                "business_fit_penalty": business_fit_penalty(row),
            }
        )

    add_method_ranks(valued)
    valued.sort(key=lambda item: item["adjusted_total_score"] if item["adjusted_total_score"] is not None else -999, reverse=True)
    csv_path = Path(f"{args.output_prefix}.csv")
    md_path = Path(f"{args.output_prefix}.md")
    write_csv(csv_path, valued)
    write_markdown(md_path, valued, assumptions)
    print(f"Wrote {csv_path}")
    print(f"Wrote {md_path}")
    return 0


def read_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    fields = [
        "valuation_rank",
        "screen_rank",
        "symbol",
        "name",
        "sector",
        "market_cap",
        "normalized_fcf",
        "fair_equity_value",
        "margin_of_safety",
        "fcf_yield",
        "implied_growth_rate",
        "dcf_margin_score",
        "fcf_yield_score",
        "reverse_dcf_score",
        "fcf_stability_score",
        "fcf_trend_score",
        "total_score",
        "business_fit_penalty",
        "adjusted_total_score",
        "positive_fcf_years",
        "rating",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for rank, row in enumerate(rows, start=1):
            valuation = row["valuation"]
            writer.writerow(
                {
                    "valuation_rank": rank,
                    "screen_rank": row["rank"],
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "sector": row["sector"],
                    "market_cap": row["market_cap"],
                    "normalized_fcf": valuation.normalized_fcf,
                    "fair_equity_value": valuation.fair_equity_value,
                    "margin_of_safety": valuation.margin_of_safety,
                    "fcf_yield": valuation.fcf_yield,
                    "implied_growth_rate": valuation.implied_growth_rate,
                    "dcf_margin_score": valuation.method_scores.get("dcf_margin") if valuation.method_scores else None,
                    "fcf_yield_score": valuation.method_scores.get("fcf_yield") if valuation.method_scores else None,
                    "reverse_dcf_score": valuation.method_scores.get("reverse_dcf") if valuation.method_scores else None,
                    "fcf_stability_score": valuation.method_scores.get("fcf_stability") if valuation.method_scores else None,
                    "fcf_trend_score": valuation.method_scores.get("fcf_trend") if valuation.method_scores else None,
                    "total_score": valuation.total_score,
                    "business_fit_penalty": row["business_fit_penalty"],
                    "adjusted_total_score": row["adjusted_total_score"],
                    "positive_fcf_years": valuation.positive_fcf_years,
                    "rating": valuation.rating,
                    "notes": "; ".join(row["screen_notes"] + valuation.notes),
                }
            )


def write_markdown(path: Path, rows: List[Dict[str, object]], assumptions: ValuationAssumptions) -> None:
    lines = [
        "# Valuation Review: Top U.S. $100M-$1B FCF Screen",
        "",
        f"- Generated: {date.today().isoformat()}",
        "- Input: top 20 companies from `us_100m_1b_top_fcf.csv`.",
        "- Method: multi-engine rank using conservative owner-earnings DCF, FCF yield, reverse DCF, FCF stability, and recent FCF trend.",
        f"- Discount rate: {percent(assumptions.discount_rate)}",
        f"- Explicit period: {assumptions.explicit_years} years",
        f"- Conservative FCF growth: {percent(assumptions.conservative_growth_rate)}",
        f"- Terminal growth: {percent(assumptions.terminal_growth_rate)}",
        "- Normalized FCF: minimum of latest FCF, last-three-year average, five-year average, and five-year median; rejected when latest FCF is negative.",
        "",
        "- Raw score weights: DCF margin 30%, FCF yield 25%, reverse DCF 20%, FCF stability 15%, FCF trend 10%.",
        "- Adjusted total score subtracts a business-fit penalty where FCF methods are less reliable: finance 25 points, homebuilders/energy cyclicals 15 points.",
        "",
        "| Total Rank | Screen Rank | Ticker | Company | Market Cap | Normalized FCF | FCF Yield | Margin | Raw Score | Fit Penalty | Adjusted Score | Rating |",
        "| ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for rank, row in enumerate(rows, start=1):
        valuation = row["valuation"]
        currency = row.get("currency") or "USD"
        lines.append(
            f"| {rank} | {row['rank']} | {row['symbol']} | {row['name']} | "
            f"{money(float(row['market_cap']), currency)} | "
            f"{money_or_na(valuation.normalized_fcf, currency)} | "
            f"{percent_or_na(valuation.fcf_yield)} | "
            f"{percent_or_na(valuation.margin_of_safety)} | "
            f"{number_or_na(valuation.total_score)} | "
            f"{number_or_na(row['business_fit_penalty'])} | "
            f"{number_or_na(row['adjusted_total_score'])} | {valuation.rating} |"
        )
    lines.extend(["", "## Method Scores", ""])
    lines.append("| Ticker | DCF Margin | FCF Yield | Reverse DCF | Stability | Trend |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for row in rows:
        valuation = row["valuation"]
        scores = valuation.method_scores or {}
        lines.append(
            f"| {row['symbol']} | {number_or_na(scores.get('dcf_margin'))} | "
            f"{number_or_na(scores.get('fcf_yield'))} | {number_or_na(scores.get('reverse_dcf'))} | "
            f"{number_or_na(scores.get('fcf_stability'))} | {number_or_na(scores.get('fcf_trend'))} |"
        )
    lines.extend(["", "## Notes", ""])
    for row in rows:
        valuation = row["valuation"]
        notes = row["screen_notes"] + valuation.notes
        if notes:
            lines.append(f"- **{row['symbol']}**: {'; '.join(notes)}")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A high margin of safety here means the market cap is below a conservative no-growth DCF based on normalized FCF.",
            "- A negative reverse DCF growth rate means the current market cap implies declining FCF from the normalized base.",
            "- This is still only a quantitative screen. Financial companies and cyclicals need business-specific review before investment decisions.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def money_or_na(value, currency: str) -> str:
    return "n/a" if value is None else money(float(value), currency)


def percent_or_na(value) -> str:
    return "n/a" if value is None else percent(float(value))


def number_or_na(value) -> str:
    return "n/a" if value is None else f"{float(value):.1f}"


def add_method_ranks(rows: List[Dict[str, object]]) -> None:
    for method in ["dcf_margin", "fcf_yield", "reverse_dcf", "fcf_stability", "fcf_trend"]:
        ranked = sorted(
            rows,
            key=lambda row: (row["valuation"].method_scores or {}).get(method) if (row["valuation"].method_scores or {}).get(method) is not None else -1,
            reverse=True,
        )
        for rank, row in enumerate(ranked, start=1):
            row[f"{method}_rank"] = rank


def screen_notes(row: Dict[str, str]) -> List[str]:
    notes = []
    sector = row.get("sector", "")
    industry = row.get("industry", "")
    if sector == "Finance":
        notes.append("Finance company: operating cash flow is not directly comparable to industrial FCF.")
    if "Homebuilding" in industry:
        notes.append("Homebuilder/cyclical: normalize through a full housing cycle before relying on FCF.")
    if "Oil & Gas" in industry:
        notes.append("Commodity/cyclical: normalize through a full commodity cycle before relying on FCF.")
    return notes


def business_fit_penalty(row: Dict[str, str]) -> float:
    penalty = 0.0
    sector = row.get("sector", "")
    industry = row.get("industry", "")
    if sector == "Finance":
        penalty += 25.0
    if "Homebuilding" in industry:
        penalty += 15.0
    if "Oil & Gas" in industry:
        penalty += 15.0
    return penalty


def adjusted_total_score(score, row: Dict[str, str]):
    if score is None:
        return None
    return max(0.0, float(score) - business_fit_penalty(row))


if __name__ == "__main__":
    raise SystemExit(main())
