import html
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
REVENUE_FACTS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
}
GENERIC_QUERY_TOKENS = {
    "co",
    "company",
    "corp",
    "corporation",
    "group",
    "holding",
    "holdings",
    "inc",
    "limited",
    "ltd",
    "motor",
    "motors",
    "plc",
}
POSITIVE_WEAKNESS_PHRASES = [
    "better functional attributes",
    "comprehensive database",
    "enables customers",
    "enhances our proprietary",
    "outclasses the competition",
    "cost savings",
    "lower carbon emissions",
    "long-term competitiveness",
    "primary destination",
    "significant opportunity",
    "technological leadership",
    "trustworthy shortlists",
]
RISK_PHRASES = [
    "adverse",
    "competition",
    "competitive pressure",
    "concentration",
    "could harm",
    "fail",
    "failure",
    "litigation",
    "material adverse",
    "regulation",
    "regulatory",
    "risk",
    "supply chain",
    "unable",
]
HARD_RISK_PHRASES = [phrase for phrase in RISK_PHRASES if phrase not in {"competition"}]


@dataclass(frozen=True)
class ReportIssue:
    code: str
    severity: str
    message: str
    location: str
    recommendation: str


@dataclass(frozen=True)
class ReportReview:
    passed: bool
    issues: List[ReportIssue]

    def to_dict(self) -> Dict[str, object]:
        return {"passed": self.passed, "issues": [asdict(issue) for issue in self.issues]}

    def to_markdown(self) -> str:
        if self.passed:
            return "## Review Gate\n\n- PASS: no report issues found.\n"
        lines = ["## Review Gate", ""]
        for issue in self.issues:
            lines.append(f"- **{issue.severity.upper()} {issue.code}** at `{issue.location}`: {issue.message}")
            lines.append(f"  - Fix: {issue.recommendation}")
        return "\n".join(lines) + "\n"


def review_report(payload: Dict[str, object], requested_company: Optional[str] = None) -> ReportReview:
    issues: List[ReportIssue] = []
    issues.extend(review_company_identity(payload, requested_company))
    issues.extend(review_filing_coverage(payload))
    issues.extend(review_annual_metrics(payload))
    issues.extend(review_evidence_quality(payload))
    return ReportReview(passed=not issues, issues=issues)


def review_company_identity(payload: Dict[str, object], requested_company: Optional[str]) -> List[ReportIssue]:
    if not requested_company:
        return []
    company = payload.get("company", {})
    requested = requested_company.strip().lower()
    ticker = str(company.get("ticker", "")).lower()
    name = str(company.get("name", "")).lower()
    if requested == ticker:
        return []

    request_tokens = set(meaningful_tokens(requested))
    resolved_tokens = set(meaningful_tokens(name))
    if request_tokens and not (request_tokens & resolved_tokens):
        return [
            ReportIssue(
                code="company_resolution_mismatch",
                severity="error",
                message=f"Requested company '{requested_company}' has no meaningful token overlap with resolved company '{company.get('name')}'.",
                location="company",
                recommendation="Tighten company resolution or rerun with ticker/CIK before trusting the report.",
            )
        ]
    return []


def review_filing_coverage(payload: Dict[str, object]) -> List[ReportIssue]:
    filings = payload.get("filings", [])
    annual_filings = [filing for filing in filings if filing.get("form") in ANNUAL_FORMS]
    if not annual_filings:
        return [
            ReportIssue(
                code="missing_annual_filings",
                severity="error",
                message="No annual report filing was included in the analysis.",
                location="filings",
                recommendation="Download at least one 10-K, 20-F, or 40-F before generating the report.",
            )
        ]
    if len(annual_filings) < min(3, len({metric.get("fiscal_year") for metric in payload.get("annual_metrics", [])})):
        return [
            ReportIssue(
                code="thin_annual_filing_coverage",
                severity="warning",
                message=f"Only {len(annual_filings)} annual filings were included.",
                location="filings",
                recommendation="Confirm that the company is newly public or expand the filing window.",
            )
        ]
    return []


def review_annual_metrics(payload: Dict[str, object]) -> List[ReportIssue]:
    metrics = payload.get("annual_metrics", [])
    filings = payload.get("filings", [])
    issues: List[ReportIssue] = []
    if not metrics:
        return [
            ReportIssue(
                code="missing_annual_metrics",
                severity="error",
                message="No annual financial metrics were extracted.",
                location="annual_metrics",
                recommendation="Inspect XBRL company facts and filing text extraction before using the report.",
            )
        ]

    seen_years = set()
    for metric in metrics:
        fiscal_year = metric.get("fiscal_year")
        if fiscal_year in seen_years:
            issues.append(
                ReportIssue(
                    code="duplicate_fiscal_year",
                    severity="error",
                    message=f"Fiscal year {fiscal_year} appears more than once.",
                    location=f"annual_metrics.{fiscal_year}",
                    recommendation="Deduplicate metrics by fiscal year before rendering.",
                )
            )
        seen_years.add(fiscal_year)

        revenue = metric.get("revenue")
        annual_revenue_values = annual_revenue_facts(filings, fiscal_year)
        if revenue is None and annual_revenue_values:
            issues.append(
                ReportIssue(
                    code="missing_revenue_despite_xbrl",
                    severity="error",
                    message=f"Revenue is n/a for {fiscal_year}, but the annual filing contains a revenue XBRL fact.",
                    location=f"annual_metrics.{fiscal_year}.revenue",
                    recommendation="Merge fallback revenue tags by year and regenerate the report.",
                )
            )
        elif revenue is not None and annual_revenue_values and max(annual_revenue_values) > revenue * 1.2:
            issues.append(
                ReportIssue(
                    code="revenue_below_xbrl_total_candidate",
                    severity="error",
                    message=(
                        f"Extracted revenue for {fiscal_year} is materially below another revenue fact in the "
                        "same annual filing."
                    ),
                    location=f"annual_metrics.{fiscal_year}.revenue",
                    recommendation="Prefer the consolidated annual revenue fact over segment or partial-period revenue facts.",
                )
            )

        issues.extend(review_cash_flow_math(metric))
    return issues


def review_cash_flow_math(metric: Dict[str, object]) -> List[ReportIssue]:
    fiscal_year = metric.get("fiscal_year")
    cfo = metric.get("operating_cash_flow")
    capex = metric.get("capital_expenditure")
    fcf = metric.get("free_cash_flow")
    revenue = metric.get("revenue")
    margin = metric.get("fcf_margin")
    issues: List[ReportIssue] = []

    if cfo is not None and capex is not None and fcf is not None:
        expected = cfo + capex if capex < 0 else cfo - capex
        tolerance = max(1.0, abs(expected) * 0.005)
        if abs(expected - fcf) > tolerance:
            issues.append(
                ReportIssue(
                    code="free_cash_flow_math_mismatch",
                    severity="error",
                    message=f"Free cash flow does not reconcile to operating cash flow minus capex for {fiscal_year}.",
                    location=f"annual_metrics.{fiscal_year}.free_cash_flow",
                    recommendation="Normalize capex sign convention and recalculate free cash flow.",
                )
            )

    if revenue not in (None, 0) and fcf is not None and margin is not None:
        expected_margin = fcf / revenue
        if abs(expected_margin - margin) > 0.0001:
            issues.append(
                ReportIssue(
                    code="fcf_margin_math_mismatch",
                    severity="error",
                    message=f"FCF margin does not reconcile to free cash flow divided by revenue for {fiscal_year}.",
                    location=f"annual_metrics.{fiscal_year}.fcf_margin",
                    recommendation="Recalculate margin from the final metric values.",
                )
            )
    return issues


def review_evidence_quality(payload: Dict[str, object]) -> List[ReportIssue]:
    samples = payload.get("evidence_samples", {})
    weaknesses = samples.get("weakness", []) if isinstance(samples, dict) else []
    issues: List[ReportIssue] = []
    for index, sentence in enumerate(weaknesses):
        lower = sentence.lower()
        has_positive = any(phrase in lower for phrase in POSITIVE_WEAKNESS_PHRASES)
        has_risk = any(phrase in lower for phrase in RISK_PHRASES)
        has_hard_risk = any(phrase in lower for phrase in HARD_RISK_PHRASES)
        if has_positive and (not has_risk or not has_hard_risk):
            issues.append(
                ReportIssue(
                    code="positive_sentence_as_weakness",
                    severity="warning",
                    message="A positive capability statement appears in Weakness Evidence.",
                    location=f"evidence_samples.weakness.{index}",
                    recommendation="Tighten risk sentence filtering and regenerate evidence samples.",
                )
            )
    return issues


def annual_revenue_facts(filings: Iterable[Dict[str, object]], fiscal_year: object) -> List[float]:
    if fiscal_year is None:
        return []
    values: List[float] = []
    for filing in filings:
        if filing.get("form") not in ANNUAL_FORMS:
            continue
        report_date = str(filing.get("report_date", ""))
        if not report_date.startswith(str(fiscal_year)):
            continue
        raw_path = filing.get("local_path")
        if raw_path and Path(str(raw_path)).exists():
            values.extend(revenue_facts_by_year(Path(str(raw_path))).get(int(fiscal_year), []))
    return values


def revenue_facts_by_year(path: Path) -> Dict[int, List[float]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    contexts = duration_contexts(text)
    results: Dict[int, List[float]] = {}
    for match in re.finditer(r"<ix:nonFraction[^>]*name=\"us-gaap:([^\"]+)\"[^>]*>", text):
        if match.group(1) not in REVENUE_FACTS:
            continue
        tag = match.group(0)
        context_match = re.search(r"contextRef=\"([^\"]+)\"", tag)
        if not context_match:
            continue
        context = contexts.get(context_match.group(1))
        if not context:
            continue
        scale_match = re.search(r"scale=\"([^\"]+)\"", tag)
        scale = int(scale_match.group(1)) if scale_match else 0
        end = text.find("</ix:nonFraction>", match.end())
        if end == -1:
            continue
        value_text = html.unescape(re.sub(r"<[^>]+>", "", text[match.end() : end]))
        value_text = value_text.replace(",", "").strip()
        try:
            value = float(value_text) * (10**scale)
        except ValueError:
            continue
        fiscal_year = int(context[1][:4])
        if context[0][:4] == context[1][:4]:
            results.setdefault(fiscal_year, []).append(value)
    return results


def duration_contexts(text: str) -> Dict[str, Tuple[str, str]]:
    contexts: Dict[str, Tuple[str, str]] = {}
    pattern = re.compile(
        r"<xbrli:context id=\"([^\"]+)\"[^>]*>.*?"
        r"<xbrli:startDate>([^<]+)</xbrli:startDate>"
        r".*?"
        r"<xbrli:endDate>([^<]+)</xbrli:endDate>.*?</xbrli:context>",
        flags=re.DOTALL,
    )
    for match in pattern.finditer(text):
        contexts[match.group(1)] = (match.group(2), match.group(3))
    return contexts


def meaningful_tokens(value: str) -> List[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value.lower()) if token not in GENERIC_QUERY_TOKENS]


def review_from_json(path: Path, requested_company: Optional[str] = None) -> ReportReview:
    return review_report(json.loads(path.read_text(encoding="utf-8")), requested_company=requested_company)
