import unittest

from stock_value_analyzer.buffett_agent import buffett_style_review
from stock_value_analyzer.models import AnnualMetric
from stock_value_analyzer.valuation import value_from_metrics


class BuffettAgentTests(unittest.TestCase):
    def test_rewards_durable_cash_generation_and_margin_of_safety(self):
        metrics = [
            AnnualMetric(2021, 900, None, None, 140, None),
            AnnualMetric(2022, 1000, None, None, 150, None),
            AnnualMetric(2023, 1100, None, None, 160, None),
            AnnualMetric(2024, 1200, None, None, 170, None),
            AnnualMetric(2025, 1300, None, None, 180, None),
        ]
        valuation = value_from_metrics(metrics, market_cap=600)

        result = buffett_style_review(
            ticker="DUR",
            company="Durable Example Corp.",
            metrics=metrics,
            valuation=valuation,
            sector="Technology",
            industry="Software",
            strength_evidence=[
                "The company benefits from brand trust, recurring usage, pricing power, scale, and high retention."
            ],
        )

        self.assertGreaterEqual(result.total_score, 80)
        self.assertEqual(result.moat_quality, 100)
        self.assertGreaterEqual(result.owner_earnings_quality, 85)
        self.assertGreaterEqual(result.valuation_discipline, 90)
        self.assertEqual(result.stance, "study_but_require_manual_verification")
        self.assertIn("not personalized financial advice", result.disclaimer)

    def test_marks_finance_as_too_hard_for_simple_fcf_screen(self):
        metrics = [
            AnnualMetric(2021, None, None, None, 80, None),
            AnnualMetric(2022, None, None, None, 90, None),
            AnnualMetric(2023, None, None, None, 100, None),
            AnnualMetric(2024, None, None, None, 110, None),
            AnnualMetric(2025, None, None, None, 120, None),
        ]
        valuation = value_from_metrics(metrics, market_cap=500)

        result = buffett_style_review(
            ticker="BNK",
            company="Bank Example Corp.",
            metrics=metrics,
            valuation=valuation,
            sector="Finance",
            industry="Consumer Finance",
        )

        self.assertEqual(result.circle_of_competence, 45)
        self.assertLess(result.balance_sheet_caution, 50)
        self.assertEqual(result.stance, "watchlist_or_too_hard")
        self.assertTrue(any("Finance sector" in note for note in result.notes))

    def test_penalizes_negative_latest_free_cash_flow(self):
        metrics = [
            AnnualMetric(2021, 500, None, None, 50, None),
            AnnualMetric(2022, 520, None, None, 60, None),
            AnnualMetric(2023, 540, None, None, 70, None),
            AnnualMetric(2024, 560, None, None, 80, None),
            AnnualMetric(2025, 580, None, None, -10, None),
        ]
        valuation = value_from_metrics(metrics, market_cap=300)

        result = buffett_style_review(
            ticker="NEG",
            company="Negative FCF Example Corp.",
            metrics=metrics,
            valuation=valuation,
            sector="Industrials",
            industry="Manufacturing",
        )

        self.assertLess(result.owner_earnings_quality, 25)
        self.assertEqual(result.valuation_discipline, 20)
        self.assertEqual(result.stance, "pass_for_now")
        self.assertTrue(any("Latest FCF is negative" in note for note in result.notes))


if __name__ == "__main__":
    unittest.main()
