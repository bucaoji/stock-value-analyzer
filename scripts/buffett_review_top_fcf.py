import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_value_analyzer.analysis import extract_annual_metrics, money, percent
from stock_value_analyzer.buffett_agent import buffett_style_review
from stock_value_analyzer.valuation import ValuationAssumptions, value_from_metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Buffett-inspired review over the top U.S. FCF screen.")
    parser.add_argument("--input", default="data/screens/us_100m_1b_top_fcf.csv")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-prefix", default="data/screens/us_100m_1b_top_fcf_buffett_review")
    args = parser.parse_args()

    rows = read_rows(Path(args.input))
    cache = Path(args.data_dir) / "screen_cache" / "companyfacts"
    reviewed = []
    assumptions = ValuationAssumptions()
    for row in rows:
        facts_path = cache / f"{row['cik']}.json"
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        metrics = extract_annual_metrics(facts, years=5)
        valuation = value_from_metrics(metrics, float(row["market_cap"]), assumptions=assumptions)
        result = buffett_style_review(
            ticker=row["symbol"],
            company=row["name"],
            metrics=metrics,
            valuation=valuation,
            sector=row.get("sector", ""),
            industry=row.get("industry", ""),
            strength_evidence=[],
        )
        reviewed.append(
            {
                **row,
                "metrics": metrics,
                "valuation": valuation,
                "buffett_result": result,
                "fit_penalty": business_fit_penalty(row),
                "adjusted_value_score": adjusted_value_score(valuation.total_score, row),
            }
        )

    reviewed.sort(
        key=lambda row: (
            row["buffett_result"].total_score,
            row["adjusted_value_score"] if row["adjusted_value_score"] is not None else -999,
        ),
        reverse=True,
    )
    csv_path = Path(f"{args.output_prefix}.csv")
    md_path = Path(f"{args.output_prefix}.md")
    write_csv(csv_path, reviewed)
    write_markdown(md_path, reviewed, assumptions)
    print(f"Wrote {csv_path}")
    print(f"Wrote {md_path}")
    return 0


def read_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    fields = [
        "buffett_rank",
        "screen_rank",
        "symbol",
        "name",
        "sector",
        "industry",
        "market_cap",
        "buffett_total_score",
        "stance",
        "circle_of_competence",
        "moat_quality",
        "owner_earnings_quality",
        "predictability",
        "balance_sheet_caution",
        "valuation_discipline",
        "temperament_fit",
        "valuation_total_score",
        "adjusted_value_score",
        "normalized_fcf",
        "margin_of_safety",
        "fcf_yield",
        "notes",
        "disclaimer",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for rank, row in enumerate(rows, start=1):
            result = row["buffett_result"]
            valuation = row["valuation"]
            writer.writerow(
                {
                    "buffett_rank": rank,
                    "screen_rank": row["rank"],
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "sector": row["sector"],
                    "industry": row["industry"],
                    "market_cap": row["market_cap"],
                    "buffett_total_score": result.total_score,
                    "stance": result.stance,
                    "circle_of_competence": result.circle_of_competence,
                    "moat_quality": result.moat_quality,
                    "owner_earnings_quality": result.owner_earnings_quality,
                    "predictability": result.predictability,
                    "balance_sheet_caution": result.balance_sheet_caution,
                    "valuation_discipline": result.valuation_discipline,
                    "temperament_fit": result.temperament_fit,
                    "valuation_total_score": valuation.total_score,
                    "adjusted_value_score": row["adjusted_value_score"],
                    "normalized_fcf": valuation.normalized_fcf,
                    "margin_of_safety": valuation.margin_of_safety,
                    "fcf_yield": valuation.fcf_yield,
                    "notes": "; ".join(result.notes),
                    "disclaimer": result.disclaimer,
                }
            )


def write_markdown(path: Path, rows: List[Dict[str, object]], assumptions: ValuationAssumptions) -> None:
    disclaimer = rows[0]["buffett_result"].disclaimer if rows else (
        "Buffett-inspired research heuristic based on public principles and filing-derived data; "
        "not Warren Buffett, not Berkshire Hathaway, and not personalized financial advice."
    )
    lines = [
        "# Buffett-Inspired Review: Top U.S. $100M-$1B FCF Screen",
        "",
        f"- Generated: {date.today().isoformat()}",
        f"- Disclaimer: {disclaimer}",
        "- Scope: screen-level review of the existing top 20 FCF candidates; it does not replace full filing review.",
        "- Lens: durable moat, owner earnings quality, predictability, conservative leverage caution, margin of safety, and temperament.",
        f"- Valuation assumptions: {percent(assumptions.discount_rate)} discount rate, {assumptions.explicit_years} years, 0% conservative growth, 0% terminal growth.",
        "",
        "| Rank | Ticker | Company | Sector | Industry | Market Cap | Buffett Score | Value Score | Stance | Normalized FCF | FCF Yield | Margin |",
        "| ---: | --- | --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |",
    ]
    for rank, row in enumerate(rows, start=1):
        result = row["buffett_result"]
        valuation = row["valuation"]
        currency = row.get("currency") or "USD"
        lines.append(
            f"| {rank} | {row['symbol']} | {row['name']} | {row['sector']} | {row['industry']} | "
            f"{money(float(row['market_cap']), currency)} | {result.total_score:.1f} | "
            f"{number_or_na(row['adjusted_value_score'])} | {result.stance} | "
            f"{money_or_na(valuation.normalized_fcf, currency)} | {percent_or_na(valuation.fcf_yield)} | "
            f"{percent_or_na(valuation.margin_of_safety)} |"
        )

    lines.extend(["", "## Agent Score Components", ""])
    lines.append("| Ticker | Circle | Moat | Owner Earnings | Predictability | Balance Sheet | Valuation | Temperament |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in rows:
        result = row["buffett_result"]
        lines.append(
            f"| {row['symbol']} | {result.circle_of_competence:.1f} | {result.moat_quality:.1f} | "
            f"{result.owner_earnings_quality:.1f} | {result.predictability:.1f} | "
            f"{result.balance_sheet_caution:.1f} | {result.valuation_discipline:.1f} | "
            f"{result.temperament_fit:.1f} |"
        )

    lines.extend(["", "## Notes", ""])
    for row in rows:
        result = row["buffett_result"]
        notes = "; ".join(result.notes)
        lines.append(f"- **{row['symbol']}**: {notes if notes else 'No automatic cautions.'}")

    lines.extend(
        [
            "",
            "## How To Use This",
            "",
            "- Treat high scores as research priority, not buy recommendations.",
            "- Require original filing review for moat, capital allocation, debt maturity, customer concentration, and management quality.",
            "- Be especially cautious when a company looks cheap because the industry is cyclical, disrupted, or hard to value with operating FCF.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def money_or_na(value, currency: str) -> str:
    return "n/a" if value is None else money(float(value), currency)


def percent_or_na(value) -> str:
    return "n/a" if value is None else percent(float(value))


def number_or_na(value) -> str:
    return "n/a" if value is None else f"{float(value):.1f}"


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


def adjusted_value_score(score, row: Dict[str, str]):
    if score is None:
        return None
    return max(0.0, float(score) - business_fit_penalty(row))


if __name__ == "__main__":
    raise SystemExit(main())
