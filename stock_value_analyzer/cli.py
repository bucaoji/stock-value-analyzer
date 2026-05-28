import argparse
from pathlib import Path
from typing import List

from .analysis import analyze_company, render_markdown
from .review import review_report
from .sec_client import SECClient
from .storage import Storage
from .text_extract import html_to_text

INTERIM_PERFORMANCE_KEYWORDS = [
    "quarterly results",
    "results for the quarter",
    "financial results",
    "three months ended",
    "six months ended",
    "nine months ended",
    "unaudited condensed consolidated",
    "unaudited results",
    "consolidated income statements",
    "consolidated statements of cash flows",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock-value-analyzer",
        description="Download SEC filings and produce long-term value analysis.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze one SEC-reporting company by name or ticker.")
    analyze.add_argument("company", help='Company name or ticker, e.g. "Apple Inc." or AAPL')
    analyze.add_argument("--years", type=int, default=5, help="Number of years of 10-K/10-Q filings to download.")
    analyze.add_argument("--data-dir", default="data", help="Directory for raw filings, catalog, and analysis output.")
    analyze.add_argument(
        "--limit-filings",
        type=int,
        default=0,
        help="Optional cap on downloaded filings. 0 means all matching filings in the period.",
    )
    analyze.add_argument(
        "--foreign-interim-limit",
        type=int,
        default=0,
        help="Maximum foreign issuer 6-K interim performance reports to keep. 0 means years * 4.",
    )
    analyze.add_argument(
        "--skip-review",
        action="store_true",
        help="Skip the deterministic report reviewer. Intended only for debugging.",
    )
    analyze.add_argument(
        "--allow-review-issues",
        action="store_true",
        help="Write a final report even when the review gate finds blocking issues.",
    )
    return parser


def main(argv: List[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "analyze":
        return run_analyze(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


def run_analyze(args) -> int:
    storage = Storage(Path(args.data_dir))
    client = SECClient()

    print(f"Resolving company: {args.company}")
    company = client.resolve_company(args.company)
    storage.upsert_company(company)
    print(f"Resolved to {company.name} ({company.ticker}), CIK {company.cik}")

    filings = client.recent_filings(company.cik, years=args.years)
    if args.limit_filings:
        filings = filings[: args.limit_filings]
    print(f"Found {len(filings)} annual/quarterly filing candidates in scope.")

    saved_filings = []
    foreign_interim_limit = args.foreign_interim_limit or args.years * 4
    saved_foreign_interims = 0
    for filing in filings:
        if filing.form == "6-K" and saved_foreign_interims >= foreign_interim_limit:
            continue
        raw_path = storage.raw_path(filing)
        if raw_path.exists():
            content = raw_path.read_bytes()
            print(f"Using cached {filing.form} filed {filing.filing_date}")
        else:
            print(f"Downloading {filing.form} filed {filing.filing_date}: {filing.url}")
            content = client.download(filing.url)
        text = html_to_text(content)
        if filing.form == "6-K" and not is_interim_performance_report(text):
            print(f"Skipping non-performance 6-K filed {filing.filing_date}")
            continue
        saved_filings.append(storage.save_filing(filing, content, text))
        if filing.form == "6-K":
            saved_foreign_interims += 1

    print(f"Saved {len(saved_filings)} report filings for analysis.")

    print("Downloading XBRL company facts.")
    companyfacts = client.get_companyfacts(company.cik)
    markdown, payload = analyze_company(company, saved_filings, companyfacts, years=args.years)

    if args.skip_review:
        output_path = storage.save_analysis(company, markdown, payload)
        print("Review skipped.")
        print(f"Analysis written to {output_path}")
        print(f"Catalog written to {storage.db_path}")
        return 0

    review = review_report(payload, requested_company=args.company)
    payload["review"] = review.to_dict()
    markdown = render_markdown(payload)
    review_path = storage.save_review(company, payload["review"])
    print(f"Review written to {review_path}")
    print_review_summary(review)

    if not review.passed and not args.allow_review_issues:
        output_path = storage.save_analysis(company, markdown, payload, status="draft")
        print(f"Draft analysis written to {output_path}")
        print("Review gate failed. Revise the script or data extraction, then regenerate the report.")
        print(f"Catalog written to {storage.db_path}")
        return 1

    output_path = storage.save_analysis(company, markdown, payload)
    print(f"Final analysis written to {output_path}")
    print(f"Catalog written to {storage.db_path}")
    return 0


def is_interim_performance_report(text: str) -> bool:
    lower_text = text.lower()
    score = sum(1 for keyword in INTERIM_PERFORMANCE_KEYWORDS if keyword in lower_text)
    return score >= 2


def print_review_summary(review) -> None:
    if review.passed:
        print("Review gate passed: no report issues found.")
        return
    error_count = sum(1 for issue in review.issues if issue.severity == "error")
    warning_count = sum(1 for issue in review.issues if issue.severity == "warning")
    print(f"Review gate found {error_count} error(s) and {warning_count} warning(s).")
    for issue in review.issues[:8]:
        print(f"- {issue.severity.upper()} {issue.code}: {issue.message}")
