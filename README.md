# Stock Value Analyzer

An evidence-first stock analysis tool for long-term investors. It takes one company
name, resolves the company through SEC data, downloads the last five years of
annual and quarterly filings, stores the raw evidence, and writes a value-oriented
analysis report.

The tool favors durable business value over market taste:

- long-term free cash flow over one-year earnings
- real competitive strengths over temporary policy advantages
- repeatable operating performance over first-mover narratives
- evidence from filings over price momentum

## Quick start

```bash
python3 -m stock_value_analyzer analyze "Apple Inc."
```

SEC asks automated tools to identify themselves. For serious use, set:

```bash
export STOCK_ANALYZER_USER_AGENT="Your Name your.email@example.com"
```

The default output lives under `data/`:

```text
data/
  catalog.sqlite              # searchable metadata catalog
  raw/sec/<cik>/<accession>/   # original downloaded filings
  extracted/sec/<cik>/         # extracted filing text
  analysis/<cik>/              # final reports, draft reports, and review results
```

## Iterative quality workflow

Each company analysis now runs through a review gate before it is treated as a
final report:

1. Analyze one company and build a draft payload from filings and XBRL facts.
2. Run a separate deterministic reviewer over the draft report and raw filing
   evidence.
3. If the reviewer finds a blocking issue, write only a draft report plus review
   JSON, fix the extraction or analysis code, and regenerate.
4. Repeat review and regeneration until the reviewer finds no blocking issue.
5. Only then write the final report and notify the user that the report is ready.

The reviewer checks for issues such as company-resolution mismatches, missing
revenue despite revenue XBRL facts in annual filings, FCF math mismatches,
margin mismatches, annual filing coverage gaps, and obvious positive statements
misclassified as weakness evidence.

For debugging only, use:

```bash
python3 -m stock_value_analyzer analyze "Apple Inc." --skip-review
```

## Valuation workflow

The project now includes a conservative multi-engine valuation path:

1. Build five-year free cash flow history from SEC XBRL facts.
2. Normalize FCF conservatively using the lowest of latest FCF, last-three-year
   average, five-year average, and five-year median.
3. Reject conservative DCF valuation when latest FCF is negative.
4. Estimate fair equity value with a no-growth DCF.
5. Run reverse DCF to estimate the FCF growth implied by current market cap.
6. Calculate margin of safety versus the conservative fair value.
7. Rank companies using multiple method scores:
   DCF margin, FCF yield, reverse DCF, FCF stability, and recent FCF trend.
8. Apply business-fit penalties when FCF methods are less reliable, such as
   finance companies, homebuilders, and commodity cyclicals.

The default assumptions are intentionally plain:

- 10% discount rate
- 10-year explicit period
- 0% conservative FCF growth
- 0% terminal growth
- 30% required margin of safety

For the small-cap screen:

```bash
python3 scripts/screen_us_fcf.py
python3 scripts/value_top_fcf_screen.py
```

The valuation output lives in:

```text
data/screens/us_100m_1b_top_fcf_valuation.md
data/screens/us_100m_1b_top_fcf_valuation.csv
```

## Buffett-inspired research agent

The project includes a separate Buffett-inspired subagent for long-term value
research. It is based on public value-investing principles commonly associated
with Warren Buffett and Berkshire Hathaway's public investment history:

- stay inside a circle of competence
- prefer durable competitive advantages over temporary or policy-created edges
- focus on owner earnings and long-term free cash flow
- demand business predictability and balance-sheet caution
- require a margin of safety
- avoid action when the business is too hard or the evidence is incomplete

This agent does not impersonate Warren Buffett or Berkshire Hathaway, and it
does not provide personalized financial advice. It produces a research stance
such as `study_but_require_manual_verification`, `watchlist_or_too_hard`, or
`pass_for_now`.

To run the agent over the current top FCF screen:

```bash
python3 scripts/buffett_review_top_fcf.py
```

The output lives in:

```text
data/screens/us_100m_1b_top_fcf_buffett_review.md
data/screens/us_100m_1b_top_fcf_buffett_review.csv
```

## Storage decision

Use a hybrid storage model:

- **File-system data lake now, object storage later** for annual and quarterly
  reports. Raw filings are immutable evidence and should be cheap, auditable,
  and easy to reprocess.
- **SQLite now, Postgres later** for company, filing, and analysis metadata.
  Metadata needs indexing and relationships, but this first version should stay
  simple and portable.
- **Markdown plus JSON analysis artifacts** for human review and future
  automation. Markdown is readable; JSON lets later agents or services consume
  the findings.
- **Vector/search index later** once the filing corpus grows and retrieval over
  report sections becomes a core feature.

Avoid putting raw reports directly into a relational database as the primary
store. It makes reprocessing and inspection clumsier. Avoid NoSQL as the default
until the access patterns are clearer.

## What the first version analyzes

- Company identity, SIC industry, and filing coverage.
- Last five years of 10-K and 10-Q SEC filings.
- Annual revenue, operating cash flow, capital expenditures, free cash flow,
  net income, and FCF margin when XBRL facts are available.
- Strengths and weaknesses based on filing evidence and long-term cash
  generation.
- Future focus areas inferred from recurring themes in the latest filings.
- Red flags around government dependence, subsidies, regulation-created
  advantages, first-mover language, concentration risk, and capital intensity.

## Notes

This tool currently focuses on SEC-reporting companies. Foreign companies or
private companies may need additional data connectors.
