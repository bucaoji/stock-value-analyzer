import tempfile
import unittest
from pathlib import Path

from stock_value_analyzer.review import review_report


def base_payload():
    return {
        "company": {"name": "Example Inc.", "ticker": "EXM", "cik": "0000000001"},
        "filings": [
            {
                "form": "10-K",
                "report_date": "2022-12-31",
                "local_path": None,
            }
        ],
        "annual_metrics": [
            {
                "fiscal_year": 2022,
                "revenue": 1000.0,
                "operating_cash_flow": 200.0,
                "capital_expenditure": 50.0,
                "free_cash_flow": 150.0,
                "fcf_margin": 0.15,
                "net_income": 100.0,
            }
        ],
        "evidence_samples": {"weakness": []},
    }


class ReviewTests(unittest.TestCase):
    def test_passes_clean_payload(self):
        review = review_report(base_payload(), requested_company="Example")

        self.assertTrue(review.passed)
        self.assertEqual(review.issues, [])

    def test_flags_missing_revenue_when_annual_filing_has_xbrl_revenue(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_path = Path(directory) / "example.htm"
            raw_path.write_text(
                """
                <xbrli:context id="ctx_2022">
                  <xbrli:entity></xbrli:entity>
                  <xbrli:period>
                    <xbrli:startDate>2022-01-01</xbrli:startDate>
                    <xbrli:endDate>2022-12-31</xbrli:endDate>
                  </xbrli:period>
                </xbrli:context>
                <ix:nonFraction contextRef="ctx_2022" name="us-gaap:Revenues" scale="0">123456</ix:nonFraction>
                """,
                encoding="utf-8",
            )
            payload = base_payload()
            payload["filings"][0]["local_path"] = str(raw_path)
            payload["annual_metrics"][0]["revenue"] = None
            payload["annual_metrics"][0]["fcf_margin"] = None

            review = review_report(payload)

            self.assertFalse(review.passed)
            self.assertIn("missing_revenue_despite_xbrl", [issue.code for issue in review.issues])

    def test_flags_cash_flow_math_mismatch(self):
        payload = base_payload()
        payload["annual_metrics"][0]["free_cash_flow"] = 10.0

        review = review_report(payload)

        self.assertFalse(review.passed)
        self.assertIn("free_cash_flow_math_mismatch", [issue.code for issue in review.issues])

    def test_flags_segment_revenue_when_larger_total_revenue_fact_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_path = Path(directory) / "example.htm"
            raw_path.write_text(
                """
                <xbrli:context id="ctx_2022">
                  <xbrli:period>
                    <xbrli:startDate>2022-01-01</xbrli:startDate>
                    <xbrli:endDate>2022-12-31</xbrli:endDate>
                  </xbrli:period>
                </xbrli:context>
                <ix:nonFraction contextRef="ctx_2022" name="us-gaap:Revenues" scale="0">1000</ix:nonFraction>
                <ix:nonFraction contextRef="ctx_2022" name="us-gaap:Revenues" scale="0">5000</ix:nonFraction>
                """,
                encoding="utf-8",
            )
            payload = base_payload()
            payload["filings"][0]["local_path"] = str(raw_path)
            payload["annual_metrics"][0]["revenue"] = 1000.0

            review = review_report(payload)

            self.assertFalse(review.passed)
            self.assertIn("revenue_below_xbrl_total_candidate", [issue.code for issue in review.issues])

    def test_flags_company_resolution_mismatch(self):
        review = review_report(base_payload(), requested_company="Lucid Motor")

        self.assertFalse(review.passed)
        self.assertIn("company_resolution_mismatch", [issue.code for issue in review.issues])

    def test_warns_when_positive_sentence_is_used_as_weakness(self):
        payload = base_payload()
        payload["evidence_samples"]["weakness"] = [
            "The Lucid Gravity outclasses the competition in a vehicle with a smaller exterior footprint."
        ]

        review = review_report(payload)

        self.assertFalse(review.passed)
        self.assertIn("positive_sentence_as_weakness", [issue.code for issue in review.issues])


if __name__ == "__main__":
    unittest.main()
