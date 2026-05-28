import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .models import AnnualMetric, Company, Filing
from .text_extract import sentences_containing


FACT_CANDIDATES = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capital_expenditure": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireOtherPropertyPlantAndEquipment",
        "CapitalExpenditures",
        "PaymentsToAcquireProductiveAssets",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
}

ANNUAL_FACT_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

FOCUS_KEYWORDS = [
    "artificial intelligence",
    "cloud",
    "subscription",
    "services",
    "research and development",
    "capital expenditure",
    "manufacturing",
    "supply chain",
    "international",
    "customer",
    "platform",
    "security",
    "data center",
    "automation",
    "margin",
]

REAL_STRENGTH_KEYWORDS = [
    "recurring",
    "retention",
    "switching costs",
    "ecosystem",
    "network effect",
    "scale",
    "brand",
    "gross margin",
    "pricing",
    "installed base",
    "proprietary",
]

WEAKNESS_KEYWORDS = [
    "competition",
    "competitive",
    "concentration",
    "supplier",
    "customer concentration",
    "litigation",
    "regulatory",
    "cybersecurity",
    "supply chain",
    "impairment",
    "macroeconomic",
    "foreign currency",
]

RISK_PATTERNS = [
    r"\bintense(?:ly)? competitive\b",
    r"\bhighly competitive\b",
    r"\bcompetitive pressure\b",
    r"\bpricing pressure\b",
    r"\bcustomer concentration\b",
    r"\bsupplier concentration\b",
    r"\blitigation\b",
    r"\bcybersecurity\b",
    r"\bimpairment\b",
    r"\bmacroeconomic\b",
    r"\bforeign currency\b",
    r"\bmaterial adverse\b",
    r"\badversely affect\b",
    r"\bunable to\b",
    r"\bfail(?:ure)? to\b",
    r"\bregulat(?:ion|ory)[^.]{0,120}\b(?:risk|cost|burden|adverse|uncertain|scrutiny|enforcement)\b",
    r"\bsupply chain[^.]{0,120}\b(?:challenge|constraint|disruption|risk|shortage|unable|adverse)\b",
]

POSITIVE_STRENGTH_CONTEXT = [
    r"\bbetter functional attributes\b",
    r"\bcomprehensive database\b",
    r"\benables? customers\b",
    r"\benhances? our proprietary\b",
    r"\boutclasses the competition\b",
    r"\bcost savings\b",
    r"\blower carbon emissions\b",
    r"\bprimary destination\b",
    r"\bsignificant opportunity\b",
    r"\blong-term competitiveness\b",
    r"\bstrengthen(?:ing)? our .*competitiveness\b",
    r"\bcompetitive strengths?\b",
    r"\binnovation capabilities\b",
    r"\bserving as the core foundation\b",
    r"\btechnological leadership\b",
    r"\btrustworthy shortlists\b",
]

TEMPORARY_ADVANTAGE_KEYWORDS = [
    "government contract",
    "government contracts",
    "subsidy",
    "subsidies",
    "tax credit",
    "incentive",
    "regulation",
    "regulatory approval",
    "tariff",
    "first mover",
    "early entrant",
]


def analyze_company(
    company: Company,
    filings: List[Filing],
    companyfacts: Dict[str, object],
    years: int,
) -> Tuple[str, Dict[str, object]]:
    filing_texts = load_filing_texts(filings)
    metrics = extract_annual_metrics(companyfacts, years=years)
    metrics = merge_reported_cash_flow_metrics(metrics, filing_texts, years=years)
    latest_annual_text = next((text for filing, text in filing_texts if filing.form == "10-K"), "")
    latest_text = " ".join(text for _, text in filing_texts[: min(len(filing_texts), 6)])

    strengths = infer_strengths(metrics, latest_text)
    weaknesses = infer_weaknesses(metrics, latest_text)
    focus_areas = infer_focus_areas(latest_text)
    temporary_advantage_flags = sentences_containing(latest_text, TEMPORARY_ADVANTAGE_KEYWORDS, limit=10)
    performance_takeaways = infer_performance(metrics)

    payload = {
        "company": {
            "name": company.name,
            "ticker": company.ticker,
            "cik": company.cik,
            "sic": company.sic,
            "sic_description": company.sic_description,
        },
        "generated_on": date.today().isoformat(),
        "storage_recommendation": storage_recommendation(),
        "filings": [
            {
                "form": filing.form,
                "filing_date": filing.filing_date,
                "report_date": filing.report_date,
                "accession_number": filing.accession_number,
                "url": filing.url,
                "local_path": str(filing.local_path) if filing.local_path else None,
                "text_path": str(filing.text_path) if filing.text_path else None,
            }
            for filing in filings
        ],
        "annual_metrics": [metric_to_dict(metric) for metric in metrics],
        "performance_takeaways": performance_takeaways,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "future_focus_areas": focus_areas,
        "temporary_advantage_flags": temporary_advantage_flags,
        "evidence_samples": {
            "strength": sentences_containing(latest_annual_text or latest_text, REAL_STRENGTH_KEYWORDS, limit=6),
            "weakness": risk_sentences(latest_annual_text or latest_text, limit=6),
        },
    }
    return render_markdown(payload), payload


def merge_reported_cash_flow_metrics(
    metrics: List[AnnualMetric],
    filing_texts: List[Tuple[Filing, str]],
    years: int,
) -> List[AnnualMetric]:
    reported = extract_reported_cash_flow_metrics(filing_texts)
    if not reported:
        return metrics

    by_year = {metric.fiscal_year: metric for metric in metrics}
    for fiscal_year, values in reported.items():
        existing = by_year.get(fiscal_year)
        currency = values.get("currency") or (existing.currency if existing else "CNY")
        cfo = values.get("operating_cash_flow") or (existing.operating_cash_flow if existing else None)
        capex = values.get("capital_expenditure")
        fcf = values.get("free_cash_flow")
        if existing:
            by_year[fiscal_year] = AnnualMetric(
                fiscal_year=fiscal_year,
                revenue=existing.revenue,
                operating_cash_flow=cfo if cfo is not None else values.get("operating_cash_flow"),
                capital_expenditure=capex if capex is not None else existing.capital_expenditure,
                free_cash_flow=fcf if fcf is not None else existing.free_cash_flow,
                net_income=existing.net_income,
                currency=currency,
            )
        else:
            by_year[fiscal_year] = AnnualMetric(
                fiscal_year=fiscal_year,
                revenue=None,
                operating_cash_flow=values.get("operating_cash_flow"),
                capital_expenditure=capex,
                free_cash_flow=fcf,
                net_income=None,
                currency=currency,
            )

    fiscal_years = sorted(by_year)[-years:]
    return [by_year[fiscal_year] for fiscal_year in fiscal_years]


def extract_reported_cash_flow_metrics(filing_texts: List[Tuple[Filing, str]]) -> Dict[int, Dict[str, object]]:
    reported: Dict[int, Dict[str, object]] = {}
    for filing, text in filing_texts:
        if filing.form != "6-K":
            continue
        for parsed in parse_free_cash_flow_reconciliation(text):
            fiscal_year = int(parsed["fiscal_year"])
            current = reported.get(fiscal_year)
            if current is None or filing.filing_date >= str(current.get("filing_date", "")):
                parsed["filing_date"] = filing.filing_date
                parsed["source_accession"] = filing.accession_number
                reported[fiscal_year] = parsed
    return reported


def parse_free_cash_flow_reconciliation(text: str) -> List[Dict[str, object]]:
    normalized = normalize_for_table_parsing(text)
    results = []
    for match in re.finditer(
        r"reconciliation of net cash provided by operating activities to free cash flow for the periods indicated:",
        normalized,
        flags=re.IGNORECASE,
    ):
        block = normalized[match.start() : match.start() + 5000]
        if not re.search(r"Year ended\s+March\s*31", block, flags=re.IGNORECASE):
            continue
        header = block[: block.find("Net cash provided by operating activities")]
        years = [int(year) for year in re.findall(r"\b(20\d{2})\b", header)]
        if len(years) < 2:
            continue

        cfo_values = values_after_label(block, "Net cash provided by operating activities")
        capex_values = values_after_label(block, "Less: Purchase of property and equipment")
        fcf_values = values_after_label(block, "Free cash flow")
        if not cfo_values or not capex_values or not fcf_values:
            continue

        annual_years, annual_indexes = annual_columns(years, cfo_values)
        if not annual_years:
            continue
        for fiscal_year, idx in zip(annual_years, annual_indexes):
            if idx >= len(cfo_values) or idx >= len(capex_values) or idx >= len(fcf_values):
                continue
            results.append(
                {
                    "fiscal_year": fiscal_year,
                    "operating_cash_flow": cfo_values[idx] * 1_000_000,
                    "capital_expenditure": abs(capex_values[idx]) * 1_000_000,
                    "free_cash_flow": fcf_values[idx] * 1_000_000,
        "currency": "CNY",
                }
            )
    return results


def normalize_for_table_parsing(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", text)


def values_after_label(block: str, label: str) -> List[float]:
    if label == "Net cash provided by operating activities":
        label_pattern = r"Net\s+cash\s+.{0,80}?operating\s+activities"
    else:
        label_pattern = re.escape(label).replace("\\ ", r"\s+")
    if label in {"Net cash provided by operating activities", "Free cash flow"}:
        label_pattern = label_pattern + r"\s+(?=\(?\s*[-\d])"
    match = re.search(
        label_pattern + r".*?(?=Less:|Free cash flow|NOTES TO|GRAPHIC|$)",
        block,
        flags=re.IGNORECASE,
    )
    if not match:
        return []
    return parse_table_numbers(match.group(0))


def parse_table_numbers(text: str) -> List[float]:
    values = []
    for token in re.finditer(r"\(?\s*-?\d[\d,]*\s*\)?|[-\u2013\u2014]", text):
        raw = token.group(0).strip()
        if raw in {"-", "\u2013", "\u2014"}:
            values.append(0.0)
            continue
        negative = raw.startswith("(") and raw.endswith(")")
        cleaned = raw.strip("() ").replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        values.append(-value if negative else value)
    return values


def annual_columns(years: List[int], values: List[float]) -> Tuple[List[int], List[int]]:
    unique_years = years[-2:]
    if len(values) >= 6 and len(years) >= 4:
        return unique_years, [len(values) - 3, len(values) - 2]
    if len(values) >= 3:
        return unique_years, [0, 1]
    if len(values) >= 2:
        return unique_years, [0, 1]
    return [], []


def extract_annual_metrics(companyfacts: Dict[str, object], years: int) -> List[AnnualMetric]:
    facts = companyfacts.get("facts", {}).get("us-gaap", {})
    currency = choose_reporting_currency(facts)
    by_name = {
        name: _extract_fact_by_year(facts, candidates, currency)
        for name, candidates in FACT_CANDIDATES.items()
    }
    fiscal_years = sorted(
        set().union(*(set(values.keys()) for values in by_name.values())),
        reverse=True,
    )[: years + 1]
    metrics = []
    for fiscal_year in sorted(fiscal_years):
        revenue = by_name["revenue"].get(fiscal_year)
        cfo = by_name["operating_cash_flow"].get(fiscal_year)
        capex = by_name["capital_expenditure"].get(fiscal_year)
        net_income = by_name["net_income"].get(fiscal_year)
        fcf = None
        if cfo is not None and capex is not None:
            fcf = cfo + capex if capex < 0 else cfo - capex
        metrics.append(AnnualMetric(fiscal_year, revenue, cfo, capex, fcf, net_income, currency))
    return metrics[-years:]


def choose_reporting_currency(facts: Dict[str, object]) -> str:
    scores = {}
    for candidates in FACT_CANDIDATES.values():
        for candidate in candidates:
            concept = facts.get(candidate)
            if not concept:
                continue
            for unit, values in concept.get("units", {}).items():
                annual_years = [
                    int(item["fy"])
                    for item in values
                    if item.get("fy") is not None
                    and item.get("fp") == "FY"
                    and str(item.get("form")) in ANNUAL_FACT_FORMS
                ]
                if not annual_years:
                    continue
                latest_year, count = scores.get(unit, (0, 0))
                scores[unit] = (max(latest_year, max(annual_years)), count + len(set(annual_years)))
    if not scores:
        return "USD"
    return max(scores.items(), key=lambda item: (item[1][0], item[1][1], item[0] != "USD"))[0]


def _extract_fact_by_year(facts: Dict[str, object], candidates: List[str], currency: str) -> Dict[int, float]:
    merged: Dict[int, Tuple[str, float]] = {}
    for candidate in candidates:
        concept = facts.get(candidate)
        if not concept:
            continue
        units = concept.get("units", {})
        usd_values = units.get(currency) or []
        by_year = {}
        for item in usd_values:
            form = item.get("form", "")
            fiscal_period = item.get("fp", "")
            fiscal_year = item.get("fy")
            value = item.get("val")
            if fiscal_year is None or value is None:
                continue
            if fiscal_period != "FY" or str(form) not in ANNUAL_FACT_FORMS:
                continue
            filed = item.get("filed", "")
            current = by_year.get(int(fiscal_year))
            if current is None or filed >= current[0]:
                by_year[int(fiscal_year)] = (filed, float(value))
        if by_year:
            for year, filed_value in by_year.items():
                merged.setdefault(year, filed_value)
    return {year: value for year, (_, value) in merged.items()}


def load_filing_texts(filings: List[Filing]) -> List[Tuple[Filing, str]]:
    loaded = []
    for filing in filings:
        if filing.text_path and Path(filing.text_path).exists():
            loaded.append((filing, Path(filing.text_path).read_text(encoding="utf-8", errors="ignore")))
    return loaded


def infer_performance(metrics: List[AnnualMetric]) -> List[str]:
    if not metrics:
        return ["No XBRL annual metric history was available for the selected company."]
    takeaways = []
    fcf_values = [metric.free_cash_flow for metric in metrics if metric.free_cash_flow is not None]
    revenue_values = [metric.revenue for metric in metrics if metric.revenue is not None]
    currency = metrics[0].currency if metrics else "USD"

    if fcf_values:
        positive_years = sum(1 for value in fcf_values if value > 0)
        takeaways.append(f"Free cash flow was positive in {positive_years} of {len(fcf_values)} measured years.")
        if len(fcf_values) >= 2:
            direction = "expanded" if fcf_values[-1] > fcf_values[0] else "contracted"
            takeaways.append(
                f"Free cash flow {direction} from {money(fcf_values[0], currency)} to {money(fcf_values[-1], currency)}."
            )
    if revenue_values and len(revenue_values) >= 2 and revenue_values[0] > 0:
        cagr = (revenue_values[-1] / revenue_values[0]) ** (1 / (len(revenue_values) - 1)) - 1
        takeaways.append(f"Revenue CAGR across the measured period was {percent(cagr)}.")

    margins = [metric.fcf_margin for metric in metrics if metric.fcf_margin is not None]
    if margins:
        takeaways.append(f"Average free-cash-flow margin was {percent(sum(margins) / len(margins))}.")
    return takeaways


def infer_strengths(metrics: List[AnnualMetric], text: str) -> List[str]:
    strengths = []
    fcf_values = [metric.free_cash_flow for metric in metrics if metric.free_cash_flow is not None]
    if fcf_values and all(value > 0 for value in fcf_values):
        strengths.append("Consistently positive free cash flow across the measured annual period.")
    if len(fcf_values) >= 3 and fcf_values[-1] > fcf_values[0]:
        strengths.append("Free cash flow expanded over the measured period, which matters more than one-year earnings noise.")
    evidence = sentences_containing(text, REAL_STRENGTH_KEYWORDS, limit=4)
    if evidence:
        strengths.append("Filings include evidence of potentially durable advantages such as scale, retention, ecosystem, pricing, or proprietary capability.")
    return strengths or ["No durable strength was inferred automatically; review the evidence sections before relying on this result."]


def infer_weaknesses(metrics: List[AnnualMetric], text: str) -> List[str]:
    weaknesses = []
    fcf_values = [metric.free_cash_flow for metric in metrics if metric.free_cash_flow is not None]
    if fcf_values and any(value < 0 for value in fcf_values):
        weaknesses.append("Free cash flow was negative in at least one measured year.")
    if len(fcf_values) >= 3 and fcf_values[-1] < fcf_values[0]:
        weaknesses.append("Free cash flow declined across the measured period.")
    capex_intensity = [
        abs(metric.capital_expenditure) / metric.revenue
        for metric in metrics
        if metric.capital_expenditure is not None and metric.revenue
    ]
    if capex_intensity and sum(capex_intensity) / len(capex_intensity) > 0.12:
        weaknesses.append("Capital expenditure intensity appears high, so reported growth may require heavy reinvestment.")
    evidence = risk_sentences(text, limit=4)
    if evidence:
        weaknesses.append("Filings contain recurring risk language around competition, concentration, regulation, supply chain, or litigation.")
    return weaknesses or ["No major weakness was inferred automatically; this should be treated as incomplete rather than clean."]


def risk_sentences(text: str, limit: int = 8) -> List[str]:
    sentence_pattern = re.compile(r"(?<=[.!?])\s+")
    sentences = sentence_pattern.split(re.sub(r"\s+", " ", text))
    hits = []
    for sentence in sentences:
        clean = sentence.strip()
        if not 80 <= len(clean) <= 700:
            continue
        if is_low_quality_evidence_sentence(clean):
            continue
        lower = clean.lower()
        if any(re.search(pattern, lower) for pattern in POSITIVE_STRENGTH_CONTEXT):
            continue
        if any(re.search(pattern, lower) for pattern in RISK_PATTERNS):
            if clean not in hits:
                hits.append(clean)
        if len(hits) >= limit:
            break
    return hits


def is_low_quality_evidence_sentence(sentence: str) -> bool:
    lower = sentence.lower()
    if "indicate by check mark" in lower:
        return True
    if "interactive data file" in lower or "regulation s-t" in lower:
        return True
    digit_count = sum(1 for char in sentence if char.isdigit())
    alpha_count = sum(1 for char in sentence if char.isalpha())
    if digit_count > 35 and digit_count > alpha_count * 0.2:
        return True
    if sentence.count(")") + sentence.count("(") > 8:
        return True
    return False


def infer_focus_areas(text: str) -> List[Dict[str, object]]:
    lower_text = text.lower()
    counts = Counter({keyword: lower_text.count(keyword) for keyword in FOCUS_KEYWORDS})
    focus = []
    for keyword, count in counts.most_common(8):
        if count <= 0:
            continue
        focus.append(
            {
                "area": keyword,
                "mentions": count,
                "evidence": sentences_containing(text, [keyword], limit=2),
            }
        )
    return focus


def storage_recommendation() -> Dict[str, str]:
    return {
        "raw_reports": "Store immutable filing originals in a file-system data lake now; move to S3/GCS/Azure Blob when the corpus grows.",
        "metadata": "Store company, filing, metric, and analysis metadata in SQLite now; migrate to Postgres when multiple users or services need concurrent access.",
        "analysis": "Store Markdown for human review and JSON for machine consumption, versioned by company and generation date.",
        "future_search": "Add a vector/search index for filing sections after the raw corpus and section parser stabilize.",
    }


def render_markdown(payload: Dict[str, object]) -> str:
    company = payload["company"]
    lines = [
        f"# {company['name']} ({company['ticker']}) Long-Term Value Analysis",
        "",
        f"- CIK: {company['cik']}",
        f"- Industry/SIC: {company.get('sic_description') or 'Unknown'} ({company.get('sic') or 'n/a'})",
        f"- Generated: {payload['generated_on']}",
        "",
        "## Storage Architecture",
    ]
    for key, value in payload["storage_recommendation"].items():
        lines.append(f"- **{key.replace('_', ' ').title()}**: {value}")

    lines.extend(["", "## Downloaded Filing Coverage"])
    for filing in payload["filings"]:
        lines.append(
            f"- {filing['form']} filed {filing['filing_date']} for period {filing['report_date']} "
            f"({filing['accession_number']})"
        )

    lines.extend(["", "## Annual Financial Metrics", ""])
    lines.append("| Fiscal Year | Revenue | Operating Cash Flow | Capex | Free Cash Flow | FCF Margin | Net Income |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for metric in payload["annual_metrics"]:
        lines.append(
            "| {fiscal_year} | {revenue} | {operating_cash_flow} | {capital_expenditure} | "
            "{free_cash_flow} | {fcf_margin} | {net_income} |".format(
                fiscal_year=metric["fiscal_year"],
                revenue=money_or_na(metric["revenue"], metric.get("currency", "USD")),
                operating_cash_flow=money_or_na(metric["operating_cash_flow"], metric.get("currency", "USD")),
                capital_expenditure=money_or_na(metric["capital_expenditure"], metric.get("currency", "USD")),
                free_cash_flow=money_or_na(metric["free_cash_flow"], metric.get("currency", "USD")),
                fcf_margin=percent_or_na(metric["fcf_margin"]),
                net_income=money_or_na(metric["net_income"], metric.get("currency", "USD")),
            )
        )

    add_list(lines, "Performance", payload["performance_takeaways"])
    add_list(lines, "Durable Strengths", payload["strengths"])
    add_list(lines, "Weaknesses And Risks", payload["weaknesses"])

    lines.extend(["", "## Future Focus Areas"])
    if payload["future_focus_areas"]:
        for item in payload["future_focus_areas"]:
            lines.append(f"- **{item['area']}**: {item['mentions']} mentions")
            for evidence in item.get("evidence", []):
                lines.append(f"  - Evidence: {evidence}")
    else:
        lines.append("- No focus areas were inferred automatically from the latest filings.")

    lines.extend(["", "## Temporary Advantage Checks"])
    if payload["temporary_advantage_flags"]:
        lines.append(
            "These excerpts mention policy, subsidy, regulation, or first-mover themes. Treat them as potential non-durable advantages until proven otherwise."
        )
        for evidence in payload["temporary_advantage_flags"]:
            lines.append(f"- {evidence}")
    else:
        lines.append("- No obvious government/subsidy/first-mover dependence was detected in the sampled filing text.")

    lines.extend(["", "## Evidence Samples"])
    add_list(lines, "Strength Evidence", payload["evidence_samples"]["strength"])
    add_list(lines, "Weakness Evidence", payload["evidence_samples"]["weakness"])
    if payload.get("review"):
        render_review(lines, payload["review"])
    lines.extend(
        [
            "",
            "## Analyst Reminder",
            "This report is a starting point, not a verdict. Before making an investment decision, review the original filings, normalize unusual cash-flow items, compare peers, and estimate owner earnings across a full business cycle.",
        ]
    )
    return "\n".join(lines) + "\n"


def add_list(lines: List[str], title: str, values: List[str]) -> None:
    lines.extend(["", f"## {title}"])
    if not values:
        lines.append("- No items.")
        return
    for value in values:
        lines.append(f"- {value}")


def render_review(lines: List[str], review: Dict[str, object]) -> None:
    lines.extend(["", "## Report Review"])
    if review.get("passed"):
        lines.append("- PASS: no report issues found.")
        return
    for issue in review.get("issues", []):
        lines.append(
            "- **{severity} {code}** at `{location}`: {message}".format(
                severity=str(issue.get("severity", "")).upper(),
                code=issue.get("code", "review_issue"),
                location=issue.get("location", "report"),
                message=issue.get("message", ""),
            )
        )
        if issue.get("recommendation"):
            lines.append(f"  - Fix: {issue['recommendation']}")


def metric_to_dict(metric: AnnualMetric) -> Dict[str, Optional[float]]:
    return {
        "fiscal_year": metric.fiscal_year,
        "revenue": metric.revenue,
        "operating_cash_flow": metric.operating_cash_flow,
        "capital_expenditure": metric.capital_expenditure,
        "free_cash_flow": metric.free_cash_flow,
        "fcf_margin": metric.fcf_margin,
        "net_income": metric.net_income,
        "currency": metric.currency,
    }


def money(value: float, currency: str = "USD") -> str:
    absolute = abs(value)
    sign = "-" if value < 0 else ""
    prefix = "$" if currency == "USD" else f"{currency} "
    if absolute >= 1_000_000_000:
        return f"{sign}{prefix}{absolute / 1_000_000_000:.1f}B"
    if absolute >= 1_000_000:
        return f"{sign}{prefix}{absolute / 1_000_000:.1f}M"
    return f"{sign}{prefix}{absolute:,.0f}"


def money_or_na(value: Optional[float], currency: str = "USD") -> str:
    return "n/a" if value is None else money(value, currency)


def percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def percent_or_na(value: Optional[float]) -> str:
    return "n/a" if value is None else percent(value)
