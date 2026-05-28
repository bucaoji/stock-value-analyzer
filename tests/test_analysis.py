import unittest

from stock_value_analyzer.analysis import (
    extract_annual_metrics,
    infer_performance,
    parse_free_cash_flow_reconciliation,
    render_markdown,
    risk_sentences,
)
from stock_value_analyzer.models import AnnualMetric
from stock_value_analyzer.sec_client import company_match_score


class AnalysisTests(unittest.TestCase):
    def test_company_match_ignores_generic_motor_only_match(self):
        query = "lucid motor"
        ford = {"title": "FORD MOTOR CO", "ticker": "F"}
        lucid = {"title": "Lucid Group, Inc.", "ticker": "LCID"}

        self.assertGreater(company_match_score(query, lucid), company_match_score(query, ford))

    def test_company_match_prefers_exact_ticker(self):
        self.assertGreater(
            company_match_score("LCID", {"title": "Lucid Group, Inc.", "ticker": "LCID"}),
            company_match_score("LCID", {"title": "L Catterton Asia Acquisition Corp", "ticker": "LCAA"}),
        )

    def test_extracts_free_cash_flow_from_companyfacts(self):
        companyfacts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"fy": 2022, "fp": "FY", "form": "10-K", "filed": "2023-01-01", "val": 1000},
                                {"fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-01-01", "val": 1200},
                            ]
                        }
                    },
                    "NetCashProvidedByUsedInOperatingActivities": {
                        "units": {
                            "USD": [
                                {"fy": 2022, "fp": "FY", "form": "10-K", "filed": "2023-01-01", "val": 200},
                                {"fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-01-01", "val": 260},
                            ]
                        }
                    },
                    "PaymentsToAcquirePropertyPlantAndEquipment": {
                        "units": {
                            "USD": [
                                {"fy": 2022, "fp": "FY", "form": "10-K", "filed": "2023-01-01", "val": 50},
                                {"fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-01-01", "val": 70},
                            ]
                        }
                    },
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [
                                {"fy": 2022, "fp": "FY", "form": "10-K", "filed": "2023-01-01", "val": 150},
                                {"fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-01-01", "val": 180},
                            ]
                        }
                    },
                }
            }
        }

        metrics = extract_annual_metrics(companyfacts, years=5)

        self.assertEqual([metric.free_cash_flow for metric in metrics], [150, 190])
        self.assertAlmostEqual(metrics[-1].fcf_margin, 190 / 1200)

    def test_extracts_missing_revenue_years_from_fallback_tags(self):
        companyfacts = {
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {
                            "USD": [
                                {"fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-01-01", "val": 3000},
                            ]
                        }
                    },
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"fy": 2021, "fp": "FY", "form": "10-K", "filed": "2022-01-01", "val": 1000},
                                {"fy": 2022, "fp": "FY", "form": "10-K", "filed": "2023-01-01", "val": 2000},
                                {"fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-01-01", "val": 3100},
                            ]
                        }
                    },
                }
            }
        }

        metrics = extract_annual_metrics(companyfacts, years=5)

        self.assertEqual([metric.revenue for metric in metrics], [1000, 2000, 3000])

    def test_performance_prefers_long_term_cash_flow(self):
        metrics = [
            AnnualMetric(2021, 1000, 180, 40, 140, 100),
            AnnualMetric(2022, 1100, 210, 50, 160, 120),
            AnnualMetric(2023, 1200, 260, 70, 190, 180),
        ]

        takeaways = infer_performance(metrics)

        self.assertTrue(any("positive in 3 of 3" in takeaway for takeaway in takeaways))
        self.assertTrue(any("expanded" in takeaway for takeaway in takeaways))

    def test_render_markdown_includes_storage_architecture(self):
        markdown = render_markdown(
            {
                "company": {
                    "name": "Example Inc.",
                    "ticker": "EXM",
                    "cik": "0000000001",
                    "sic": "3571",
                    "sic_description": "Electronic Computers",
                },
                "generated_on": "2026-05-27",
                "storage_recommendation": {
                    "raw_reports": "Store raw reports in a data lake.",
                    "metadata": "Store metadata in SQLite.",
                },
                "filings": [],
                "annual_metrics": [],
                "performance_takeaways": [],
                "strengths": [],
                "weaknesses": [],
                "future_focus_areas": [],
                "temporary_advantage_flags": [],
                "evidence_samples": {"strength": [], "weakness": []},
            }
        )

        self.assertIn("## Storage Architecture", markdown)
        self.assertIn("Store raw reports in a data lake.", markdown)

    def test_parses_alibaba_style_annual_free_cash_flow_reconciliation(self):
        text = """
        The table below sets forth a reconciliation of net cash provided by
        operating activities to free cash flow for the periods indicated:
        Three months ended March 31, Year ended March 31, 2025 2026 2025 2026
        RMB RMB US$ RMB RMB US$ (in millions) (in millions)
        Net cash provided by operating activities 27,520 9,410 1,364 163,509 76,213 11,049
        Less: Purchase of property and equipment (excluding land use rights and construction in progress relating to office campuses)
        (23,993) (26,588) (3,854) (84,278) (122,021) (17,689)
        Less: Purchase of intangible assets (excluding those acquired through acquisitions) - (874) (127) - (874) (127)
        Less: Changes in the buyer protection fund deposits 216 752 109 (5,361) 73 10
        Free cash flow 3,743 (17,300) (2,508) 73,870 (46,609) (6,757)
        """

        parsed = parse_free_cash_flow_reconciliation(text)

        by_year = {item["fiscal_year"]: item for item in parsed}
        self.assertEqual(by_year[2025]["capital_expenditure"], 84_278_000_000)
        self.assertEqual(by_year[2026]["capital_expenditure"], 122_021_000_000)
        self.assertEqual(by_year[2026]["free_cash_flow"], -46_609_000_000)
        self.assertEqual(by_year[2026]["currency"], "CNY")

    def test_parses_cash_flow_row_with_used_in_parenthetical(self):
        text = """
        reconciliation of net cash provided by operating activities to free cash flow for the periods indicated:
        Three months ended March 31, Year ended March 31, 2021 2022 2021 2022
        RMB RMB US$ RMB RMB US$ (in millions) (in millions)
        Net cash provided by (used in) operating activities 24,183 (7,040) (1,111) 231,786 142,759 22,520
        Less: Purchase of property and equipment (excluding land use rights and construction in progress relating to office campuses)
        (6,043) (9,201) (1,451) (36,160) (42,028) (6,630)
        Less: Acquisition of intangible assets (2) - - (1,735) (15) (2)
        Less: Changes in the consumer protection fund deposits (18,796) 1,171 185 (21,229) (1,842) (291)
        Free cash flow (658) (15,070) (2,377) 172,662 98,874 15,597
        """

        parsed = parse_free_cash_flow_reconciliation(text)

        by_year = {item["fiscal_year"]: item for item in parsed}
        self.assertEqual(by_year[2022]["operating_cash_flow"], 142_759_000_000)
        self.assertEqual(by_year[2022]["capital_expenditure"], 42_028_000_000)
        self.assertEqual(by_year[2022]["free_cash_flow"], 98_874_000_000)

    def test_risk_sentences_exclude_positive_competitiveness_claims(self):
        text = (
            "After eleven years of sustained investment and dedication, the Company has built comprehensive "
            "systematic innovation capabilities, serving as the core foundation for us to continuously launch "
            "innovative products and strengthen our long-term competitiveness. "
            "If our competitors introduce new vehicles or services that successfully compete with or surpass "
            "the quality or performance of our offerings at more competitive prices, we may be unable to satisfy "
            "existing customers or attract new customers at acceptable margins."
        )

        risks = risk_sentences(text)

        self.assertEqual(len(risks), 1)
        self.assertIn("competitors introduce new vehicles", risks[0])

    def test_risk_sentences_exclude_positive_competition_claims(self):
        text = (
            "Our technology and the Lucid Space Concept enable us to provide better functional attributes than "
            "competitors who rely on platforms that must accommodate hybridized and internal combustion propulsion systems. "
            "The Lucid Gravity builds upon this design philosophy to deliver space for occupants and cargo that "
            "outclasses the competition in a vehicle with a significantly smaller exterior footprint. "
            "If we fail to scale production and service operations, our business and financial condition could be adversely affected."
        )

        risks = risk_sentences(text)

        self.assertEqual(len(risks), 1)
        self.assertIn("fail to scale production", risks[0])

    def test_risk_sentences_exclude_positive_supply_chain_claims(self):
        text = (
            "There is a significant opportunity to help companies around the world improve their supply chain diversification, "
            "access just-in-time production, and build supply chain resilience. "
            "This growth enhances our proprietary data flywheel, enabling more accurate pricing and more resilient supply chain execution. "
            "Supply chain disruptions or supplier constraints could adversely affect our ability to fulfill customer orders."
        )

        risks = risk_sentences(text)

        self.assertEqual(len(risks), 1)
        self.assertIn("Supply chain disruptions", risks[0])

    def test_risk_sentences_exclude_sec_boilerplate_and_table_fragments(self):
        text = (
            "Indicate by check mark whether the registrant has submitted electronically every Interactive Data File "
            "required to be submitted pursuant to Rule 405 of Regulation S-T during the preceding 12 months. "
            "(6,891,061 ) 122,443 (496,008 ) (71,906 ) Net loss attributable to ordinary shareholders and total "
            "comprehensive loss table values are presented for accounting purposes. "
            "Failure of this market to grow at the projected rate may have a material adverse effect on our business "
            "and the market price of our ADSs or Class A ordinary shares."
        )

        risks = risk_sentences(text)

        self.assertEqual(risks, [(
            "Failure of this market to grow at the projected rate may have a material adverse effect on our business "
            "and the market price of our ADSs or Class A ordinary shares."
        )])


if __name__ == "__main__":
    unittest.main()
