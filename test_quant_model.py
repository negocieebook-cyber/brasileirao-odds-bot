import unittest

from brasileirao_bot import estimar_1x2_poisson, calcular_recomendacao_modelo


class QuantModelTests(unittest.TestCase):
    def test_poisson_probabilities_sum_to_one(self):
        probs = estimar_1x2_poisson(1.45, 0.95)

        self.assertAlmostEqual(sum(probs.values()), 1.0, places=6)
        self.assertGreater(probs["home"], probs["away"])

    def test_value_recommendation_uses_quarter_kelly_and_requires_edge(self):
        rec = calcular_recomendacao_modelo(
            selection="Londrina vence",
            odds=2.10,
            probability=0.55,
            match="Londrina vs Ponte Preta",
            source="ESPN results model",
        )

        self.assertIsNotNone(rec)
        self.assertEqual(rec["data_status"], "verified")
        self.assertTrue(rec["price_verified"])
        self.assertGreaterEqual(rec["ev"], 0.05)
        self.assertGreater(rec["stake_pct"], 0)

    def test_no_bet_when_model_probability_has_no_edge(self):
        rec = calcular_recomendacao_modelo(
            selection="Londrina vence",
            odds=2.10,
            probability=0.47,
            match="Londrina vs Ponte Preta",
            source="ESPN results model",
        )

        self.assertIsNone(rec)


if __name__ == "__main__":
    unittest.main()
