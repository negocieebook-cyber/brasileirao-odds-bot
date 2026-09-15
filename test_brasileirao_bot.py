import unittest
from datetime import datetime, timezone, timedelta

from brasileirao_bot import (
    dividir_mensagem,
    montar_mensagem,
    jogos_da_semana,
    recomendacoes_verificadas,
    utc_to_brt,
)

BRT = timezone(timedelta(hours=-3))


def evento(nome, inicio, odds=True):
    runners = [
        {"name": "Casa", "prices": [{"side": "back", "odds": 2.1}]},
        {"name": "Draw", "prices": [{"side": "back", "odds": 3.2}]},
        {"name": "Fora", "prices": [{"side": "back", "odds": 3.7}]},
    ] if odds else []
    return {
        "name": nome,
        "start": inicio.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "_start_brt": inicio.astimezone(BRT),
        "volume": 10000,
        "event-participants": [],
        "markets": [{"market-type": "one_x_two", "runners": runners}],
        "meta-tags": [{"type": "COMPETITION", "url-name": "brazil-serie-a"}],
    }


class DailyBulletinTests(unittest.TestCase):
    def test_long_message_is_split_without_exceeding_telegram_limit(self):
        message = "A" * 4090 + "\n" + "B" * 40

        chunks = dividir_mensagem(message)

        self.assertEqual("".join(chunks), message)
        self.assertTrue(all(len(chunk) <= 4096 for chunk in chunks))

    def test_message_has_required_sections_and_safe_no_bet_default(self):
        now = datetime(2026, 9, 15, 8, 30, tzinfo=BRT)
        today = evento("Casa vs Fora", now.replace(hour=19))
        weekly = [today, evento("Time A vs Time B", now + timedelta(days=3))]

        message = montar_mensagem([today], now, weekly_games=weekly)

        self.assertIn("<b>MELHORES APOSTAS</b>", message)
        self.assertIn("Nenhuma aposta VERIFICADA", message)
        self.assertIn("<b>JOGOS DO DIA</b>", message)
        self.assertIn("<b>JOGOS DA SEMANA</b>", message)
        self.assertIn("INFORMAÇÕES / DESFALQUES: <i>não verificados</i>", message)

    def test_weekly_games_excludes_events_after_seven_days(self):
        now = datetime(2026, 9, 15, 8, 30, tzinfo=BRT)
        inside = evento("Dentro vs Jogo", now + timedelta(days=6))
        outside = evento("Fora vs Janela", now + timedelta(days=7, minutes=1))

        result = jogos_da_semana([inside, outside], now)

        self.assertEqual([x["name"] for x in result], ["Dentro vs Jogo"])

    def test_recommendation_requires_verified_price_probability_and_positive_stake(self):
        raw = [
            {"selection": "Casa vence", "odds": 2.10, "model_probability": 0.55,
             "stake_pct": 1.25, "price_verified": True, "data_status": "verified"},
            {"selection": "Fora vence", "odds": 3.70, "model_probability": 0.30,
             "stake_pct": 1.00, "price_verified": False, "data_status": "verified"},
        ]

        result = recomendacoes_verificadas(raw)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["selection"], "Casa vence")


if __name__ == "__main__":
    unittest.main()
