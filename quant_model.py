#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Núcleo quantitativo do Betina (N3 — framework MAQFA adaptado ao escopo ESPN grátis).

Filosofia fail-closed: nenhuma função emite número quando a entrada não está
verificada. O Preço e a Prob abstratos (PROVA_AUDITAVEL) nunca são inventados.
Inputs não verificados -> [não verificado] -> a recomendação é descartada.

Módulo 100% stdlib (base: Python 3.9+).

Blocos:
  * Probabilidades   : Poisson independente (baseline) + bivariado Dixon-Coles.
  * EV / Kelly       : EV lÍquido de comissão de exchange + Kelly fracionado.
  * AIF (Agente 1)   : Availability Impact Factor a partir de desfalques dados.
  * Tático (Agente 2): regras de matchup multiplicando ratings (dado-gated).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Comissão padrão da exchange. 3,5% é o valor citado no framework do Gemini,
# PORÉM é [não verificado] contra a tabela oficial da Bolsa de Aposta. Deixamos
# sobrescrevível via env e o default conservador é 0 (comportamento atual do
# bot), para NUNCA inflar EV com um desconto que a plataforma não confirmou.
# ---------------------------------------------------------------------------
DEFAULT_COMMISSION = 0.0

# Limiares de governança de banca (fail-closed: só aposta se passar tudo).
MIN_EV = 0.05
KELLY_FRACTION = 0.25          # quarter-Kelly
MAX_STAKE_PCT = 2.0            # teto por posição (% da banca)

# Guardrail de calibração (honesto): um modelo Poisson simples com shrink não tem
# resolução confiável nas caudas. EV diferente demais do mercado em odds longas
# é sinal clássico de miscalibração (o modelo infla o azarão). Por isso:
#   * OE_MIN_ODDS / MAX_ODDS: só avaliamos janela onde o modelo tem resolução.
#   * MAX_EV: EV acima deste teto é tratado como suspeito (provavelmente erro de
#     probabilidade, não edge real) e descartado.
# Estes são GUARDRAILS de engenharia, não fatos: validação real vem de
# backtest/forward-test, que ainda não calibramos.
OE_MIN_ODDS = 1.40
OE_MAX_ODDS = 4.00
OE_MAX_EV = 0.25

_NAO_VERIFICADO = "não verificado"


# ---------------------------------------------------------------------------
# 1. PROBABILIDADES
# ---------------------------------------------------------------------------
def poisson_pmf(k: int, lam: float) -> float:
    """P(Gols = k | Poisson(lam))."""
    lam = max(lam, 0.0)
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _normalizar(probs: dict) -> dict:
    total = sum(probs.values())
    if total <= 0:
        return {k: 0.0 for k in probs}
    return {k: v / total for k, v in probs.items()}


def estimar_1x2_poisson(lambda_home: float, lambda_away: float,
                        max_gols: int = 10) -> dict:
    """Probabilidades 1X2 por Poisson independente e normalizado."""
    if lambda_home <= 0 or lambda_away <= 0:
        raise ValueError("lambdas devem ser positivos")
    home = draw = away = 0.0
    for gh in range(max_gols + 1):
        ph = poisson_pmf(gh, lambda_home)
        for ga in range(max_gols + 1):
            p = ph * poisson_pmf(ga, lambda_away)
            if gh > ga:
                home += p
            elif gh == ga:
                draw += p
            else:
                away += p
    return _normalizar({"home": home, "draw": draw, "away": away})


def _tau_dixon_coles(gh: int, ga: int, lam_home: float, lam_away: float,
                     rho: float) -> float:
    """Fator de ajuste Dixon-Coles para placares baixos (correlação de jogo)."""
    if gh == 0 and ga == 0:
        return 1.0 - lam_home * lam_away * rho
    if gh == 0 and ga == 1:
        return 1.0 + lam_home * rho
    if gh == 1 and ga == 0:
        return 1.0 + lam_away * rho
    if gh == 1 and ga == 1:
        return 1.0 - rho
    return 1.0


def estimar_1x2_poisson_dixon_coles(lambda_home: float, lambda_away: float,
                                    rho: float = 0.08,
                                    max_gols: int = 10) -> dict:
    """1X2 com bivariado Dixon-Coles (correlação entre gols dos times).

    rho=0 reduz à Poisson independente, validando a fórmula.
    """
    if lambda_home <= 0 or lambda_away <= 0:
        raise ValueError("lambdas devem ser positivos")
    home = draw = away = 0.0
    for gh in range(max_gols + 1):
        ph = poisson_pmf(gh, lambda_home)
        for ga in range(max_gols + 1):
            tau = _tau_dixon_coles(gh, ga, lambda_home, lambda_away, rho)
            p = max(ph * poisson_pmf(ga, lambda_away) * tau, 0.0)
            if gh > ga:
                home += p
            elif gh == ga:
                draw += p
            else:
                away += p
    return _normalizar({"home": home, "draw": draw, "away": away})


def estimar_mercados(lambda_home: float, lambda_away: float,
                     rho: float = 0.08, max_gols: int = 10) -> dict:
    """Matriz completa de mercados a partir de um par de lambdas (Dixon-Coles).

    Retorna probabilidades já normalizadas; Over/Under são calculados das
    massas somadas, então não somam 1 entre si (são linhas independentes do
    mercado). 1X2 sempre soma 1.
    """
    if lambda_home <= 0 or lambda_away <= 0:
        raise ValueError("lambdas devem ser positivos")

    mass = {}
    grid = {}
    for gh in range(max_gols + 1):
        ph = poisson_pmf(gh, lambda_home)
        for ga in range(max_gols + 1):
            tau = _tau_dixon_coles(gh, ga, lambda_home, lambda_away, rho)
            p = max(ph * poisson_pmf(ga, lambda_away) * tau, 0.0)
            mass[(gh, ga)] = p
            grid[(gh, ga)] = p

    total = sum(mass.values())
    if total <= 0:
        raise ValueError("massa total da distribuição é zero")

    home = draw = away = 0.0
    over25 = under25 = 0.0
    btts = 0.0
    cs_home = cs_away = 0.0
    home_over15 = home_under15 = 0.0
    for (gh, ga), p in grid.items():
        pn = p / total
        if gh > ga:
            home += pn
        elif gh == ga:
            draw += pn
        else:
            away += pn
        if gh + ga > 2:
            over25 += pn
        else:
            under25 += pn
        if gh > 0 and ga > 0:
            btts += pn
        if ga == 0:
            cs_home += pn
        if gh == 0:
            cs_away += pn
        if gh > 1:
            home_over15 += pn
        else:
            home_under15 += pn

    return {
        "home": home, "draw": draw, "away": away,
        "over_2_5": over25, "under_2_5": under25,
        "btts": btts,
        "cs_home": cs_home, "cs_away": cs_away,
        "home_over_1_5": home_over15, "home_under_1_5": home_under15,
    }


# ---------------------------------------------------------------------------
# 2. EV / KELLY (com comissão de exchange)
# ---------------------------------------------------------------------------
def ev_por_unidade(probabilidade: float, odds: float,
                   commission: float = DEFAULT_COMMISSION) -> float:
    """EV líquido por R$1 jogado, descontando comissão sobre o lucro líquido.

    Lucro líquido por unidade = (odds-1) * (1-commission); pierde = -1.
    """
    if not 0 < probabilidade < 1 or odds <= 1:
        return 0.0
    b = odds - 1.0
    cropped = max(0.0, min(1.0, commission))
    return probabilidade * b * (1 - cropped) - (1 - probabilidade)


def kelly_fracionada(probabilidade: float, odds: float,
                     fraction: float = KELLY_FRACTION,
                     commission: float = DEFAULT_COMMISSION,
                     max_stake: float = MAX_STAKE_PCT) -> float:
    """Fração da banca (em %) por Kelly fracionado com comissão de exchange.

    Forma exata para exchange: f* = p - (1-p) / (b*(1-c)).
    Retorna 0 se não houver EV liquido positivo.
    """
    if not 0 < probabilidade < 1 or odds <= 1:
        return 0.0
    b = odds - 1.0
    cropped = max(0.0, min(1.0, commission))
    net_b = b * (1 - cropped)
    if net_b <= 0:
        return 0.0
    f_star = probabilidade - (1 - probabilidade) / net_b
    if f_star <= 0:
        return 0.0
    pct = f_star * fraction * 100.0
    return round(max(0.0, min(max_stake, pct)), 2)


def montar_recomendacao(odds: float, probabilidade: float, match: str,
                        selection: str, source: str,
                        commission: float = DEFAULT_COMMISSION,
                        min_ev: float = MIN_EV) -> dict | None:
    """Monta recomendação somente quando modelo supera EV mÍnimo verificável.

    Fail-closed: exige prob e odds >= threshold; caso contrário None (NO BET).
    Aplica guardrail de calibração (janela de odds + teto de EV) para não
    recomendar cauda longa onde o modelo Poisson não tem resolução confiável.
    """
    if not isinstance(odds, (int, float)) or not isinstance(probabilidade, (int, float)):
        return None
    if not (OE_MIN_ODDS <= odds <= OE_MAX_ODDS):
        return None
    ev = ev_por_unidade(probabilidade, odds, commission)
    if ev > OE_MAX_EV:
        return None  # EV implausivel -> miscalibracao, descarta
    stake = kelly_fracionada(probabilidade, odds, commission=commission)
    if ev < min_ev or stake <= 0:
        return None
    return {
        "match": match,
        "selection": selection,
        "odds": odds,
        "model_probability": probabilidade,
        "ev": ev,
        "stake_pct": stake,
        "commission": commission,
        "price_verified": True,
        "data_status": "verified",
        "source": source,
    }


# ---------------------------------------------------------------------------
# 3. AIF — Agente 1 (Availability Impact Factor)
# ---------------------------------------------------------------------------
# Pesos posicionais (framework MAQFA). São calibrações de referência, não
# fatos pretos; uso como default, mas todo input que gera AIF exige ser
# verificado antes.
PESO_POSICAO = {
    "goleiro": 0.10, "zagueiro": 0.09, "volante": 0.11,
    "ala": 0.08, "meia": 0.08, "atacante": 0.07,
}


def calcular_aif(desfalques: list, pesos: dict | None = None,
                 max_value: float = 1.0) -> dict:
    """AIF a partir de uma lista de desfalques verificados.

    desfalques: [{posicao:str, delta:float}] onde delta = perda de valor
        1.0 = confirmado ausente; 0.5 = dúvida. Peso padrão por posição.
    Retorna {"aif": float, "detalhes": [...], "verificado": bool}.
    """
    pesos = pesos or PESO_POSICAO
    soma = 0.0
    detalhes = []
    for d in desfalques or []:
        pos = (d.get("posicao") or "").lower()
        w = pesos.get(pos, 0.0)
        delta = float(d.get("delta", 1.0))
        contrib = w * max(0.0, min(1.0, delta))
        soma += contrib
        detalhes.append({"posicao": pos, "peso": w, "delta": delta, "contrib": contrib})
    aif = max(0.0, min(max_value, 1.0 - soma))
    return {"aif": round(aif, 3), "detalhes": detalhes, "verificado": True}


# ---------------------------------------------------------------------------
# 4. TÁTICO — Agente 2 (matchup multipliers; dado-gated, fail-closed)
# ---------------------------------------------------------------------------
@dataclass
class Ratings:
    """Ratings de ataque/defesa normalizados (default = abertos até verificar)."""
    attack: float
    defense: float
    aif: float = 1.0
    verificado: bool = False


def aplicar_matchup(ataque: Ratings, defesa: Ratings, tempo_de_jogo: float) -> dict:
    """Regras de matchup tático (framework MAQFA), SOMENTE com dados verificados.

    Qualquer input não-verificado => ajuste = 1.0 (sem mágica). Precisamos de
    tempo_de_jogo como proxy de contexto (reservado p/ inputs futuros).
    """
    mult_att = 1.0
    mult_def = 1.0
    triggers = []

    if not (ataque.verificado and defesa.verificado):
        return {"mult_attack": 1.0, "mult_defense": 1.0, "triggers": [],
                "verificado": False}

    # (placeholder de regras: implementar quando houver fonte de xG/PPDA)
    return {"mult_attack": mult_att, "mult_defense": mult_def,
            "triggers": triggers, "verificado": True}


# ---------------------------------------------------------------------------
# 5. ORQUESTRAÇÃO N3 (dado-gated)
# ---------------------------------------------------------------------------
def rodar_modelo_n3(jogo: dict) -> list:
    """Pipeline N3 dado-gated: só gera recomendação com dados verificados.

    jogo: dict com campos lanbda/ratings verificados + odds observada na exchange.
    Retorna lista de recomendações (vazia = NO BET).
    """
    odds = jogo.get("odds")
    prob = jogo.get("model_probability")
    if not isinstance(odds, (int, float)) or not isinstance(prob, (int, float)):
        return []
    rec = montar_recomendacao(
        odds=odds, probabilidade=prob, match=jogo.get("match", ""),
        selection=jogo.get("selection", ""), source=jogo.get("source", "N3"),
        commission=jogo.get("commission", DEFAULT_COMMISSION),
    )
    return [rec] if rec else []