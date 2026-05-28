import unittest

from stock_value_analyzer.models import AnnualMetric
from stock_value_analyzer.valuation import (
    ValuationAssumptions,
    discounted_cash_flow,
    normalized_free_cash_flow,
    reverse_dcf_growth,
    score_methods,
    value_from_metrics,
)


class ValuationTests(unittest.TestCase):
    def test_normalized_fcf_uses_conservative_low_reference(self):
        self.assertEqual(normalized_free_cash_flow([100, 120, 140, 160, 300]), 140)

    def test_normalized_fcf_rejects_negative_latest_fcf(self):
        self.assertIsNone(normalized_free_cash_flow([100, 120, 140, 160, -10]))

    def test_dcf_with_zero_growth_is_ten_times_fcf_at_ten_percent_discount(self):
        value = discounted_cash_flow(100, growth_rate=0.0, discount_rate=0.10, terminal_growth_rate=0.0, years=10)

        self.assertAlmostEqual(value, 1000, places=6)

    def test_reverse_dcf_solves_current_market_growth(self):
        implied = reverse_dcf_growth(1000, 100, discount_rate=0.10, terminal_growth_rate=0.0, years=10)

        self.assertAlmostEqual(implied, 0.0, places=6)

    def test_value_from_metrics_rates_deep_value_candidate(self):
        metrics = [
            AnnualMetric(2021, None, None, None, 100, None),
            AnnualMetric(2022, None, None, None, 110, None),
            AnnualMetric(2023, None, None, None, 120, None),
            AnnualMetric(2024, None, None, None, 130, None),
            AnnualMetric(2025, None, None, None, 140, None),
        ]

        result = value_from_metrics(metrics, market_cap=500, assumptions=ValuationAssumptions())

        self.assertEqual(result.rating, "deep_value_candidate")
        self.assertGreater(result.margin_of_safety, 1)
        self.assertIsNotNone(result.total_score)
        self.assertEqual(result.method_scores["fcf_stability"], 100)

    def test_score_methods_rewards_cheap_consistent_fcf(self):
        scores = score_methods(
            margin_of_safety=1.0,
            fcf_yield=0.20,
            implied_growth_rate=-0.05,
            fcf_stability=1.0,
            latest_fcf_growth=0.10,
            positive_fcf_years=5,
            fcf_years=5,
            latest_fcf=100,
        )

        self.assertEqual(scores["dcf_margin"], 100)
        self.assertEqual(scores["fcf_yield"], 100)
        self.assertEqual(scores["reverse_dcf"], 100)


if __name__ == "__main__":
    unittest.main()
