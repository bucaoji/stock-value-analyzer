from dataclasses import asdict, dataclass
from statistics import mean, median
from typing import Dict, Iterable, List, Optional

from .models import AnnualMetric


@dataclass(frozen=True)
class ValuationAssumptions:
    discount_rate: float = 0.10
    explicit_years: int = 10
    conservative_growth_rate: float = 0.00
    terminal_growth_rate: float = 0.00
    margin_of_safety_required: float = 0.30


@dataclass(frozen=True)
class ValuationResult:
    normalized_fcf: Optional[float]
    fair_equity_value: Optional[float]
    current_market_cap: Optional[float]
    margin_of_safety: Optional[float]
    implied_growth_rate: Optional[float]
    positive_fcf_years: int
    fcf_years: int
    fcf_values: List[float]
    rating: str
    notes: List[str]
    assumptions: Dict[str, float]
    fcf_yield: Optional[float] = None
    fcf_stability: Optional[float] = None
    latest_fcf_growth: Optional[float] = None
    method_scores: Optional[Dict[str, Optional[float]]] = None
    total_score: Optional[float] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def value_from_metrics(
    metrics: Iterable[AnnualMetric],
    market_cap: Optional[float],
    assumptions: ValuationAssumptions = ValuationAssumptions(),
) -> ValuationResult:
    fcf_values = [metric.free_cash_flow for metric in metrics if metric.free_cash_flow is not None]
    positive_years = sum(1 for value in fcf_values if value > 0)
    notes: List[str] = []
    normalized = normalized_free_cash_flow(fcf_values)
    if normalized is None:
        method_scores = score_methods(
            margin_of_safety=None,
            fcf_yield=None,
            implied_growth_rate=None,
            fcf_stability=fcf_stability(fcf_values),
            latest_fcf_growth=latest_fcf_growth(fcf_values),
            positive_fcf_years=positive_years,
            fcf_years=len(fcf_values),
            latest_fcf=fcf_values[-1] if fcf_values else None,
        )
        return ValuationResult(
            normalized_fcf=None,
            fair_equity_value=None,
            current_market_cap=market_cap,
            margin_of_safety=None,
            implied_growth_rate=None,
            positive_fcf_years=positive_years,
            fcf_years=len(fcf_values),
            fcf_values=fcf_values,
            rating="not_valued",
            notes=["Insufficient or non-positive free cash flow history for a conservative DCF."],
            assumptions=asdict(assumptions),
            fcf_yield=None,
            fcf_stability=fcf_stability(fcf_values),
            latest_fcf_growth=latest_fcf_growth(fcf_values),
            method_scores=method_scores,
            total_score=total_score(method_scores),
        )

    if len(fcf_values) < 5:
        notes.append("Fewer than five FCF years were available.")
    if positive_years < len(fcf_values):
        notes.append("FCF was not positive in every measured year.")
    if fcf_values[-1] <= 0:
        notes.append("Latest FCF was negative, so normalized FCF was not accepted.")

    fair_value = discounted_cash_flow(
        normalized,
        growth_rate=assumptions.conservative_growth_rate,
        discount_rate=assumptions.discount_rate,
        terminal_growth_rate=assumptions.terminal_growth_rate,
        years=assumptions.explicit_years,
    )
    margin = None
    implied = None
    if market_cap and market_cap > 0:
        margin = (fair_value - market_cap) / market_cap
        implied = reverse_dcf_growth(
            market_cap,
            normalized,
            discount_rate=assumptions.discount_rate,
            terminal_growth_rate=assumptions.terminal_growth_rate,
            years=assumptions.explicit_years,
        )
    yield_value = normalized / market_cap if market_cap and market_cap > 0 else None
    stability = fcf_stability(fcf_values)
    growth = latest_fcf_growth(fcf_values)
    method_scores = score_methods(
        margin_of_safety=margin,
        fcf_yield=yield_value,
        implied_growth_rate=implied,
        fcf_stability=stability,
        latest_fcf_growth=growth,
        positive_fcf_years=positive_years,
        fcf_years=len(fcf_values),
        latest_fcf=fcf_values[-1],
    )

    return ValuationResult(
        normalized_fcf=normalized,
        fair_equity_value=fair_value,
        current_market_cap=market_cap,
        margin_of_safety=margin,
        implied_growth_rate=implied,
        positive_fcf_years=positive_years,
        fcf_years=len(fcf_values),
        fcf_values=fcf_values,
        rating=rating(margin, positive_years, len(fcf_values), fcf_values[-1]),
        notes=notes,
        assumptions=asdict(assumptions),
        fcf_yield=yield_value,
        fcf_stability=stability,
        latest_fcf_growth=growth,
        method_scores=method_scores,
        total_score=total_score(method_scores),
    )


def normalized_free_cash_flow(fcf_values: List[float]) -> Optional[float]:
    if len(fcf_values) < 3:
        return None
    if fcf_values[-1] <= 0:
        return None
    recent_average = mean(fcf_values[-3:])
    five_year_average = mean(fcf_values)
    base = min(fcf_values[-1], recent_average, five_year_average, median(fcf_values))
    if base <= 0:
        return None
    return base


def discounted_cash_flow(
    starting_fcf: float,
    growth_rate: float,
    discount_rate: float,
    terminal_growth_rate: float,
    years: int,
) -> float:
    if discount_rate <= terminal_growth_rate:
        raise ValueError("discount_rate must be greater than terminal_growth_rate")
    value = 0.0
    fcf = starting_fcf
    for year in range(1, years + 1):
        fcf *= 1 + growth_rate
        value += fcf / ((1 + discount_rate) ** year)
    terminal_value = fcf * (1 + terminal_growth_rate) / (discount_rate - terminal_growth_rate)
    value += terminal_value / ((1 + discount_rate) ** years)
    return value


def reverse_dcf_growth(
    market_cap: float,
    starting_fcf: float,
    discount_rate: float,
    terminal_growth_rate: float,
    years: int,
) -> Optional[float]:
    if starting_fcf <= 0 or market_cap <= 0:
        return None
    low = -0.50
    high = min(0.50, discount_rate - 0.001)
    for _ in range(80):
        mid = (low + high) / 2
        value = discounted_cash_flow(
            starting_fcf,
            growth_rate=mid,
            discount_rate=discount_rate,
            terminal_growth_rate=terminal_growth_rate,
            years=years,
        )
        if value < market_cap:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def fcf_stability(fcf_values: List[float]) -> Optional[float]:
    if not fcf_values:
        return None
    return sum(1 for value in fcf_values if value > 0) / len(fcf_values)


def latest_fcf_growth(fcf_values: List[float]) -> Optional[float]:
    if len(fcf_values) < 2 or fcf_values[-2] <= 0:
        return None
    return (fcf_values[-1] / fcf_values[-2]) - 1


def score_methods(
    margin_of_safety: Optional[float],
    fcf_yield: Optional[float],
    implied_growth_rate: Optional[float],
    fcf_stability: Optional[float],
    latest_fcf_growth: Optional[float],
    positive_fcf_years: int,
    fcf_years: int,
    latest_fcf: Optional[float],
) -> Dict[str, Optional[float]]:
    scores = {
        "dcf_margin": score_margin_of_safety(margin_of_safety),
        "fcf_yield": score_fcf_yield(fcf_yield),
        "reverse_dcf": score_reverse_dcf(implied_growth_rate),
        "fcf_stability": None if fcf_stability is None else max(0.0, min(100.0, fcf_stability * 100)),
        "fcf_trend": score_fcf_growth(latest_fcf_growth),
    }
    if latest_fcf is not None and latest_fcf <= 0:
        scores["fcf_trend"] = 0.0
    if fcf_years and positive_fcf_years < fcf_years:
        scores["fcf_stability"] = min(scores["fcf_stability"] or 0.0, (positive_fcf_years / fcf_years) * 100)
    return scores


def score_margin_of_safety(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return max(0.0, min(100.0, value / 1.0 * 100))


def score_fcf_yield(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return max(0.0, min(100.0, value / 0.20 * 100))


def score_reverse_dcf(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if value <= 0:
        return 100.0
    return max(0.0, min(100.0, (0.15 - value) / 0.15 * 100))


def score_fcf_growth(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return max(0.0, min(100.0, ((value + 0.20) / 0.60) * 100))


def total_score(scores: Dict[str, Optional[float]]) -> Optional[float]:
    weights = {
        "dcf_margin": 0.30,
        "fcf_yield": 0.25,
        "reverse_dcf": 0.20,
        "fcf_stability": 0.15,
        "fcf_trend": 0.10,
    }
    available = [(key, score) for key, score in scores.items() if score is not None]
    if not available:
        return None
    total_weight = sum(weights[key] for key, _ in available)
    return sum((score or 0.0) * weights[key] for key, score in available) / total_weight


def rating(
    margin_of_safety: Optional[float],
    positive_years: int,
    fcf_years: int,
    latest_fcf: float,
) -> str:
    if latest_fcf <= 0:
        return "avoid_until_fcf_recovers"
    if margin_of_safety is None:
        return "watch"
    if positive_years < fcf_years:
        return "watch_cyclical_or_inconsistent"
    if margin_of_safety >= 0.50:
        return "deep_value_candidate"
    if margin_of_safety >= 0.30:
        return "value_candidate"
    if margin_of_safety >= 0:
        return "fair_to_slight_discount"
    return "over_conservative_value"
