import json
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, Optional

from .models import Company, Filing


class Storage:
    def __init__(self, root: Path):
        self.root = root
        self.raw_root = root / "raw" / "sec"
        self.extracted_root = root / "extracted" / "sec"
        self.analysis_root = root / "analysis"
        self.db_path = root / "catalog.sqlite"
        self._init_dirs()
        self._init_db()

    def _init_dirs(self) -> None:
        for path in [self.raw_root, self.extracted_root, self.analysis_root]:
            path.mkdir(parents=True, exist_ok=True)

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS companies (
                    cik TEXT PRIMARY KEY,
                    ticker TEXT,
                    name TEXT NOT NULL,
                    sic TEXT,
                    sic_description TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS filings (
                    accession_number TEXT PRIMARY KEY,
                    cik TEXT NOT NULL,
                    form TEXT NOT NULL,
                    filing_date TEXT NOT NULL,
                    report_date TEXT,
                    primary_document TEXT,
                    url TEXT,
                    local_path TEXT,
                    text_path TEXT,
                    downloaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(cik) REFERENCES companies(cik)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cik TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    json_path TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(cik) REFERENCES companies(cik)
                )
                """
            )

    def upsert_company(self, company: Company) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO companies (cik, ticker, name, sic, sic_description, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(cik) DO UPDATE SET
                    ticker=excluded.ticker,
                    name=excluded.name,
                    sic=excluded.sic,
                    sic_description=excluded.sic_description,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (company.cik, company.ticker, company.name, company.sic, company.sic_description),
            )

    def raw_path(self, filing: Filing) -> Path:
        return self.raw_root / filing.cik / filing.accession_number.replace("-", "") / filing.primary_document

    def text_path(self, filing: Filing) -> Path:
        return (
            self.extracted_root
            / filing.cik
            / f"{filing.filing_date}_{filing.form}_{filing.accession_number.replace('-', '')}.txt"
        )

    def save_filing(self, filing: Filing, content: bytes, text: str) -> Filing:
        raw_path = self.raw_path(filing)
        text_path = self.text_path(filing)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        if not raw_path.exists():
            raw_path.write_bytes(content)
        text_path.write_text(text, encoding="utf-8")
        saved = Filing(
            cik=filing.cik,
            accession_number=filing.accession_number,
            form=filing.form,
            filing_date=filing.filing_date,
            report_date=filing.report_date,
            primary_document=filing.primary_document,
            url=filing.url,
            local_path=raw_path,
            text_path=text_path,
        )
        self.upsert_filing(saved)
        return saved

    def upsert_filing(self, filing: Filing) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO filings (
                    accession_number, cik, form, filing_date, report_date,
                    primary_document, url, local_path, text_path, downloaded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(accession_number) DO UPDATE SET
                    form=excluded.form,
                    filing_date=excluded.filing_date,
                    report_date=excluded.report_date,
                    primary_document=excluded.primary_document,
                    url=excluded.url,
                    local_path=excluded.local_path,
                    text_path=excluded.text_path,
                    downloaded_at=CURRENT_TIMESTAMP
                """,
                (
                    filing.accession_number,
                    filing.cik,
                    filing.form,
                    filing.filing_date,
                    filing.report_date,
                    filing.primary_document,
                    filing.url,
                    str(filing.local_path) if filing.local_path else None,
                    str(filing.text_path) if filing.text_path else None,
                ),
            )

    def analysis_paths(self, company: Company, status: str = "final"):
        directory = self.analysis_root / company.cik
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = company.name.lower().replace(" ", "_").replace("/", "_")
        suffix = "analysis" if status == "final" else f"analysis_{status}"
        return directory / f"{safe_name}_{suffix}.md", directory / f"{safe_name}_{suffix}.json"

    def review_path(self, company: Company) -> Path:
        directory = self.analysis_root / company.cik
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = company.name.lower().replace(" ", "_").replace("/", "_")
        return directory / f"{safe_name}_review.json"

    def save_analysis(
        self,
        company: Company,
        markdown: str,
        payload: Dict[str, object],
        status: str = "final",
    ) -> Path:
        markdown_path, json_path = self.analysis_paths(company, status=status)
        markdown_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO analyses (cik, company_name, markdown_path, json_path)
                VALUES (?, ?, ?, ?)
                """,
                (company.cik, company.name, str(markdown_path), str(json_path)),
            )
        return markdown_path

    def save_review(self, company: Company, review: Dict[str, object]) -> Path:
        path = self.review_path(company)
        path.write_text(json.dumps(review, indent=2, sort_keys=True), encoding="utf-8")
        return path
