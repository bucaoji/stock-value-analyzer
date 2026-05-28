from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional

from .models import AnnualMetric
from .valuation import ValuationResult


MOAT_WORDS = [
    "brand",
    "ecosystem",
    "installed base",
    "network effect",
    "pricing",
    "proprietary",
    "recurring",
    "retention",
    "scale",
    "switching cost",
]

TOO_HARD_SECTORS = {"finance"}
CYCLICAL_INDUSTRY_WORDS = ["homebuilding", "oil & gas", "metals", "mining", "steel", "autos"]
DECLINING_OR_DISRUPTION_WORDS = ["print", "broadcast", "cable", "media", "legacy"]


@dataclass(frozen=True)
class BuffettAgentResult:
    company: str
    ticker: str
    circle_of_competence: float
    moat_quality: float
    owner_earnings_quality: float
    predictability: float
    balance_sheet_caution: float
    valuation_discipline: float
    temperament_fit: float
    total_score: float
    stance: str
    notes: List[str]
    disclaimer: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def buffett_style_review(
    ticker: str,
    company: str,
    metrics: Iterable[AnnualMetric],
    valuation: ValuationResult,
    sector: str = "",
    industry: str = "",
    strength_evidence: Optional[List[str]] = None,
) -> BuffettAgentResult:
    metrics = list(metrics)
    fcf_values = [metric.free_cash_flow for metric in metrics if metric.free_cash_flow is not None]
    revenue_values = [metric.revenue for metric in metrics if metric.revenue is not None]
    notes: List[str] = []

    circle = circle_of_competence_score(sector, industry, notes)
    moat = moat_score(strength_evidence or [], sector, industry, notes)
    owner = owner_earnings_score(fcf_values, valuation, notes)
    predict = predictability_score(revenue_values, fcf_values, notes)
    balance = balance_sheet_caution_score(sector, industry, valuation, notes)
    valuation_score = valuation_discipline_score(valuation, notes)
    temperament = temperament_score(valuation, fcf_values, notes)

    total = weighted_score(
        {
            "circle": (circle, 0.15),
            "moat": (moat, 0.18),
            "owner": (owner, 0.22),
            "predict": (predict, 0.15),
            "balance": (balance, 0.10),
            "valuation": (valuation_score, 0.15),
            "temperament": (temperament, 0.05),
        }
    )
    return BuffettAgentResult(
        company=company,
        ticker=ticker,
        circle_of_competence=circle,
        moat_quality=moat,
        owner_earnings_quality=owner,
        predictability=predict,
        balance_sheet_caution=balance,
        valuation_discipline=valuation_score,
        temperament_fit=temperament,
        total_score=total,
        stance=investment_stance(total, notes),
        notes=notes,
        disclaimer=(
            "Buffett-inspired research heuristic based on public principles and filing-derived data; "
            "not Warren Buffett, not Berkshire Hathaway, and not personalized financial advice."
        ),
    )


def circle_of_competence_score(sector: str, industry: str, notes: List[str]) -> float:
    sector_key = sector.lower()
    industry_key = industry.lower()
    if sector_key in TOO_HARD_SECTORS:
        notes.append("Finance sector: requires specialist credit/book-value analysis before relying on FCF.")
        return 45.0
    if any(word in industry_key for word in CYCLICAL_INDUSTRY_WORDS):
        notes.append("Cyclical industry: use mid-cycle owner earnings, not recent peak cash flow.")
        return 55.0
    if any(word in industry_key for word in DECLINING_OR_DISRUPTION_WORDS):
        notes.append("Potentially disrupted/declining industry: demand stronger evidence of durable cash flow.")
        return 55.0
    return 75.0


def moat_score(evidence: List[str], sector: str, industry: str, notes: List[str]) -> float:
    combined = " ".join(evidence).lower()
    hits = sum(1 for word in MOAT_WORDS if word in combined)
    score = min(100.0, 35.0 + hits * 12.0)
    if hits == 0:
        notes.append("No strong moat evidence was detected from the available evidence samples.")
    if sector.lower() == "technology" and hits >= 2:
        score += 5.0
    return min(100.0, score)


def owner_earnings_score(fcf_values: List[float], valuation: ValuationResult, notes: List[str]) -> float:
    if not fcf_values:
        notes.append("No FCF record available.")
        return 0.0
    positive_ratio = sum(1 for value in fcf_values if value > 0) / len(fcf_values)
    score = positive_ratio * 65.0
    if valuation.normalized_fcf and valuation.normalized_fcf > 0:
        score += 25.0
    if fcf_values[-1] <= 0:
        notes.append("Latest FCF is negative.")
        score -= 35.0
    elif len(fcf_values) >= 2 and fcf_values[-1] < fcf_values[-2]:
        notes.append("Latest FCF declined year over year.")
        score -= 10.0
    return clamp(score)


def predictability_score(revenue_values: List[float], fcf_values: List[float], notes: List[str]) -> float:
    score = 60.0
    if len(revenue_values) >= 2 and revenue_values[-1] > revenue_values[0]:
        score += 15.0
    elif len(revenue_values) >= 2:
        notes.append("Revenue did not expand across the measured period.")
        score -= 15.0
    if len(fcf_values) >= 5 and all(value > 0 for value in fcf_values):
        score += 20.0
    elif fcf_values:
        notes.append("FCF record is not consistently positive.")
        score -= 20.0
    return clamp(score)


def balance_sheet_caution_score(
    sector: str,
    industry: str,
    valuation: ValuationResult,
    notes: List[str],
) -> float:
    score = 65.0
    industry_key = industry.lower()
    if sector.lower() == "finance":
        score -= 20.0
    if any(word in industry_key for word in CYCLICAL_INDUSTRY_WORDS):
        score -= 15.0
    if valuation.rating == "not_valued":
        score -= 25.0
    notes.append("Debt maturity and balance-sheet stress are not yet fully modeled by this agent.")
    return clamp(score)


def valuation_discipline_score(valuation: ValuationResult, notes: List[str]) -> float:
    margin = valuation.margin_of_safety
    if margin is None:
        notes.append("No conservative DCF margin of safety was available.")
        return 20.0
    if margin >= 1.0:
        return 95.0
    if margin >= 0.5:
        return 80.0
    if margin >= 0.3:
        return 70.0
    if margin >= 0:
        notes.append("Margin of safety is thin under conservative assumptions.")
        return 50.0
    notes.append("Market cap exceeds conservative no-growth FCF value.")
    return 25.0


def temperament_score(valuation: ValuationResult, fcf_values: List[float], notes: List[str]) -> float:
    if valuation.implied_growth_rate is not None and valuation.implied_growth_rate <= 0 and fcf_values and fcf_values[-1] > 0:
        return 85.0
    if valuation.implied_growth_rate is not None and valuation.implied_growth_rate > 0.10:
        notes.append("Current price requires meaningful future FCF growth.")
        return 35.0
    return 60.0


def weighted_score(values: Dict[str, tuple]) -> float:
    return sum(score * weight for score, weight in values.values()) / sum(weight for _, weight in values.values())


def investment_stance(total_score: float, notes: List[str]) -> str:
    text = " ".join(notes).lower()
    if total_score >= 55 and (
        "finance sector" in text
        or "cyclical industry" in text
        or "potentially disrupted/declining industry" in text
    ):
        return "watchlist_or_too_hard"
    if total_score >= 78 and "not yet fully modeled" not in text:
        return "study_as_possible_long_term_candidate"
    if total_score >= 70:
        return "study_but_require_manual_verification"
    if total_score >= 55:
        return "watchlist_or_too_hard"
    return "pass_for_now"


def clamp(value: float) -> float:
    return max(0.0, min(100.0, value))
