import unittest

from quant_model import (
    DEFAULT_COMMISSION, MAX_STAKE_PCT, MIN_EV,
    calcular_aif, estimar_1x2_poisson, estimar_1x2_poisson_dixon_coles,
    estimar_mercados, ev_por_unidade, kelly_fracionada,
    montar_recomendacao, rodar_modelo_n3,
)


class ProbabilidadesTests(unittest.TestCase):
    def test_poisson_1x2_soma_um(self):
        probs = estimar_1x2_poisson(1.45, 0.95)
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=6)
        self.assertGreater(probs["home"], probs["away"])

    def test_dixon_coles_rho0_equivale_a_poisson(self):
        for lam_h, lam_a in [(1.2, 0.8), (1.6, 1.6), (0.9, 1.9)]:
            base = estimar_1x2_poisson(lam_h, lam_a)
            dc_zero = estimar_1x2_poisson_dixon_coles(lam_h, lam_a, rho=0.0)
            for k in ("home", "draw", "away"):
                self.assertAlmostEqual(base[k], dc_zero[k], places=6)

    def test_distribuicao_1x2_soma_um(self):
        merc = estimar_mercados(1.4, 1.1)
        self.assertAlmostEqual(merc["home"] + merc["draw"] + merc["away"], 1.0, places=6)

    def test_mercados_totais_complementares(self):
        merc = estimar_mercados(1.4, 1.1)
        self.assertGreater(merc["home"], 0)
        self.assertGreater(merc["under_2_5"], 0)
        # over_2.5 e under_2.5, sem a linha exatamente=2 em exatos 2 = nao somam 1
        self.assertAlmostEqual(
            merc["over_2_5"] + merc["under_2_5"], 1.0, places=6)


class EVKellyTests(unittest.TestCase):
    def test_ev_sem_comissao(self):
        # p=0.55, odds=2.10, c=0 -> EV = 0.55*1.10 - 0.45 = 0.155
        ev = ev_por_unidade(0.55, 2.10, commission=0.0)
        self.assertAlmostEqual(ev, 0.155, places=6)

    def test_ev_com_comissao_reduz_retorno(self):
        ev_sem = ev_por_unidade(0.55, 2.10, commission=0.0)
        ev_com = ev_por_unidade(0.55, 2.10, commission=0.035)
        self.assertLess(ev_com, ev_sem)
        # verificado manualmente: p*(b*(1-c)) - (1-p)
        b = 2.10 - 1
        esperado = 0.55 * b * (1 - 0.035) - 0.45
        self.assertAlmostEqual(ev_com, esperado, places=6)

    def test_ev_odds_1_sem_valor(self):
        self.assertEqual(ev_por_unidade(0.6, 1.0), 0.0)

    def test_kelly_fracionada_nao_negativa(self):
        self.assertEqual(kelly_fracionada(0.40, 2.10), 0.0)   # sem edge
        k = kelly_fracionada(0.55, 2.10)
        self.assertGreater(k, 0)
        # quarter-Kelly pode estourar o teto; o teto manda
        self.assertLessEqual(k, MAX_STAKE_PCT)

    def test_kelly_respeita_teto_de_banca(self):
        # edge grande -> quarter-Kelly bruto > 2% -> capado em MAX_STAKE_PCT
        self.assertEqual(kelly_fracionada(0.75, 3.00), MAX_STAKE_PCT)

    def test_kelly_com_comissao_menor_que_sem(self):
        # p/0.50 em odds 2.10 o quarter-Kelly fica abaixo do teto -> comparavel
        self.assertLess(
            kelly_fracionada(0.50, 2.10, commission=0.035),
            kelly_fracionada(0.50, 2.10, commission=0.0),
        )


class RecomendacaoTests(unittest.TestCase):
    def test_montar_recomendacao_sem_edge_retorna_none(self):
        self.assertIsNone(montar_recomendacao(2.10, 0.47, "A vs B", "A vence", "x"))

    def test_montar_recomendacao_com_edge(self):
        rec = montar_recomendacao(2.10, 0.55, "A vs B", "A vence", "ESPN model")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["data_status"], "verified")
        self.assertGreaterEqual(rec["ev"], MIN_EV)
        self.assertGreater(rec["stake_pct"], 0)

    def test_guardrail_descarta_cauda_longa(self):
        # prob alta o suficiente p/ EV enorme em odd 10.5 -> miscalibracao, descarta
        self.assertIsNone(montar_recomendacao(10.50, 0.30, "A vs B", "sorte", "x"))

    def test_guardrail_descarta_ev_implausivel(self):
        # dentro da janela de odds, mas EV > teto -> suspeito, descarta
        self.assertIsNone(montar_recomendacao(3.50, 0.80, "A vs B", "favorito", "x"))

    def test_rodar_modelo_n3_sem_dados_return_vazio(self):
        self.assertEqual(rodar_modelo_n3({}), [])


class AIFTests(unittest.TestCase):
    def test_aif_sem_desfalque_e_um(self):
        r = calcular_aif([])
        self.assertEqual(r["aif"], 1.0)
        self.assertTrue(r["verificado"])

    def test_aif_com_ausencia_confirmada_reduz(self):
        r = calcular_aif([{"posicao": "volante", "delta": 1.0}])
        self.assertAlmostEqual(r["aif"], 1.0 - 0.11, places=3)

    def test_aif_dubio_reduz_menos(self):
        confirmado = calcular_aif([{"posicao": "atacante", "delta": 1.0}])
        duvida = calcular_aif([{"posicao": "atacante", "delta": 0.5}])
        self.assertGreater(confirmado["aif"], 0)
        self.assertGreater(duvida["aif"], confirmado["aif"])

    def test_aif_nunca_negativo(self):
        muitos = [
            {"posicao": p, "delta": 1.0} for p in ("volante", "zagueiro", "goleiro", "atacante")
        ]
        r = calcular_aif(muitos)
        self.assertGreaterEqual(r["aif"], 0.0)


class ShrinkageTests(unittest.TestCase):
    def _shrink(self, media, n, base=1.30, k=30):
        from brasileirao_bot import _com_shrinkage
        return _com_shrinkage(media, n, base, k)

    def test_shrinkage_puxa_curto_para_base(self):
        # media alta com amostra curta -> volta pra perto do baseline
        self.assertGreater(self._shrink(2.5, 5, base=1.30), self._shrink(2.5, 5, base=1.30) * 0)  # sanidade
        self.assertLess(self._shrink(2.5, 5, base=1.30), 2.5)
        self.assertGreater(self._shrink(2.5, 5, base=1.30), 1.30)

    def test_shrinkage_amostra_grande_quase_sem_efeito(self):
        curto = self._shrink(2.5, 5, base=1.30)
        longo = self._shrink(2.5, 500, base=1.30)
        # quanto maior a amostra, mais proximo da media real (2.5) do que a curta
        self.assertLess(abs(longo - 2.5), abs(curto - 2.5))

    def test_shrinkage_media_fraca_puxa_para_cima_nao_para_baixo(self):
        # underdog fraco (media baixa) e amostra curta -> sobe em direcao ao baseline
        self.assertGreater(self._shrink(0.5, 5, base=1.30), 0.5)
        self.assertLess(self._shrink(0.5, 5, base=1.30), 1.30)


if __name__ == "__main__":
    unittest.main()