import difflib
import json
import os
import re
import time
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .models import Company, Filing


SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts"

DOMESTIC_REPORT_FORMS = {"10-K", "10-Q"}
FOREIGN_ANNUAL_REPORT_FORMS = {"20-F", "40-F"}
FOREIGN_INTERIM_REPORT_FORMS = {"6-K"}
GENERIC_COMPANY_TERMS = {
    "adr",
    "ads",
    "class",
    "co",
    "company",
    "corp",
    "corporation",
    "group",
    "holding",
    "holdings",
    "inc",
    "incorporated",
    "limited",
    "ltd",
    "motor",
    "motors",
    "plc",
    "sa",
}


def normalized_tokens(value: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", value.lower())


def meaningful_tokens(value: str) -> List[str]:
    return [token for token in normalized_tokens(value) if token not in GENERIC_COMPANY_TERMS]


def company_match_score(query: str, row: Dict[str, object]) -> float:
    title = str(row.get("title", "")).lower()
    ticker = str(row.get("ticker", "")).lower()
    query = query.strip().lower()
    if not query:
        return 0.0
    if query == ticker:
        return 10.0

    title_tokens = set(normalized_tokens(title))
    query_meaningful = set(meaningful_tokens(query))
    title_meaningful = set(meaningful_tokens(title))
    meaningful_overlap = query_meaningful & title_meaningful

    if query_meaningful and query_meaningful <= title_meaningful:
        return 6.0 + difflib.SequenceMatcher(None, query, title).ratio()

    if query_meaningful:
        overlap_ratio = len(meaningful_overlap) / len(query_meaningful)
        token_score = 4.0 * overlap_ratio
    else:
        shared_tokens = set(normalized_tokens(query)) & title_tokens
        token_score = 1.0 if shared_tokens else 0.0

    name_score = difflib.SequenceMatcher(None, query, title).ratio()
    ticker_score = difflib.SequenceMatcher(None, query, ticker).ratio()
    if not meaningful_overlap and query_meaningful:
        name_score *= 0.35
        ticker_score *= 0.35

    return max(token_score + name_score, ticker_score)


class SECClient:
    def __init__(self, user_agent: Optional[str] = None, sleep_seconds: float = 0.15):
        self.user_agent = user_agent or os.environ.get(
            "STOCK_ANALYZER_USER_AGENT",
            "stock-value-analyzer/0.1 research-tool contact@example.com",
        )
        self.sleep_seconds = sleep_seconds

    def resolve_company(self, company_name: str) -> Company:
        tickers = self._get_json(SEC_COMPANY_TICKERS_URL)
        rows = list(tickers.values())
        best = max(rows, key=lambda row: company_match_score(company_name, row))
        if company_match_score(company_name, best) < 0.35:
            raise ValueError(f"Could not confidently resolve company name: {company_name}")

        cik = str(best["cik_str"]).zfill(10)
        submissions = self.get_submissions(cik)
        return Company(
            cik=cik,
            ticker=str(best.get("ticker", "")),
            name=str(best.get("title", "")),
            sic=str(submissions.get("sic") or "") or None,
            sic_description=str(submissions.get("sicDescription") or "") or None,
        )

    def get_submissions(self, cik: str) -> Dict[str, object]:
        return self._get_json(f"{SEC_SUBMISSIONS_URL}/CIK{cik.zfill(10)}.json")

    def get_companyfacts(self, cik: str) -> Dict[str, object]:
        return self._get_json(f"{SEC_COMPANYFACTS_URL}/CIK{cik.zfill(10)}.json")

    def recent_filings(self, cik: str, years: int = 5) -> List[Filing]:
        submissions = self.get_submissions(cik)
        recent = submissions.get("filings", {}).get("recent", {})
        cutoff = date.today() - timedelta(days=365 * years + 45)
        filings: List[Filing] = []
        forms = set(recent.get("form", []))
        is_foreign_issuer = bool(forms & FOREIGN_ANNUAL_REPORT_FORMS)
        report_forms = set(DOMESTIC_REPORT_FORMS)
        if is_foreign_issuer:
            report_forms.update(FOREIGN_ANNUAL_REPORT_FORMS)
            report_forms.update(FOREIGN_INTERIM_REPORT_FORMS)

        for idx, form in enumerate(recent.get("form", [])):
            if form not in report_forms:
                continue
            filing_date = recent["filingDate"][idx]
            if date.fromisoformat(filing_date) < cutoff:
                continue
            accession = recent["accessionNumber"][idx]
            primary_document = recent["primaryDocument"][idx]
            accession_path = accession.replace("-", "")
            if form in FOREIGN_INTERIM_REPORT_FORMS:
                primary_document = f"{accession}.txt"
            url = f"{SEC_ARCHIVES_URL}/{int(cik)}/{accession_path}/{primary_document}"
            filings.append(
                Filing(
                    cik=cik.zfill(10),
                    accession_number=accession,
                    form=form,
                    filing_date=filing_date,
                    report_date=recent["reportDate"][idx],
                    primary_document=primary_document,
                    url=url,
                )
            )

        filings.sort(key=lambda filing: filing.filing_date, reverse=True)
        return filings

    def download(self, url: str) -> bytes:
        return self._get_bytes(url)

    def _get_json(self, url: str) -> Dict[str, object]:
        return json.loads(self._get_bytes(url).decode("utf-8"))

    def _get_bytes(self, url: str) -> bytes:
        time.sleep(self.sleep_seconds)
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "identity",
                "Accept": "application/json,text/html,application/xhtml+xml,*/*",
            },
        )
        try:
            with urlopen(request, timeout=45) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code in {403, 429}:
                raise RuntimeError(
                    "SEC rejected or throttled the request. Set STOCK_ANALYZER_USER_AGENT "
                    "to your name and email, then retry more slowly."
                ) from exc
            raise
