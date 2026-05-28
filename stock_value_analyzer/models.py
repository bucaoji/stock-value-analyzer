from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Company:
    cik: str
    ticker: str
    name: str
    sic: Optional[str] = None
    sic_description: Optional[str] = None


@dataclass(frozen=True)
class Filing:
    cik: str
    accession_number: str
    form: str
    filing_date: str
    report_date: str
    primary_document: str
    url: str
    local_path: Optional[Path] = None
    text_path: Optional[Path] = None


@dataclass(frozen=True)
class AnnualMetric:
    fiscal_year: int
    revenue: Optional[float]
    operating_cash_flow: Optional[float]
    capital_expenditure: Optional[float]
    free_cash_flow: Optional[float]
    net_income: Optional[float]
    currency: str = "USD"

    @property
    def fcf_margin(self) -> Optional[float]:
        if self.revenue in (None, 0) or self.free_cash_flow is None:
            return None
        return self.free_cash_flow / self.revenue
