#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚽ Brasileirão Odds Bot
======================
Busca os jogos do dia de TIMES brasileiros na bolsa de apostas
(Bolsa de Aposta / exchange) com as odds de Match Odds (1X2),
e envia um resumo formatado para o Telegram.

Cobre todas as competições domésticas (Brasileirão A/B/C/D, Copa do
Brasil, estaduais) e jogos internacionais de clubes brasileiros
(Libertadores, Sul-Americana).

Uso:
    python brasileirao_bot.py                      # mostra a mensagem (dry-run)
    python brasileirao_bot.py --envio              # envia para todos os destinatários
    python brasileirao_bot.py --boas-vindas        # boas-vindas p/ todos os destinatários
    python brasileirao_bot.py --boas-vindas 123456 # boas-vindas p/ um novo destinatário

Configuração (variáveis de ambiente ou arquivo .env ao lado do script):
    BETBOT_TELEGRAM_TOKEN  token do bot
    BETBOT_CHAT_ID         ids separados por vírgula

Sem dependências externas — apenas biblioteca padrão do Python 3.9+.
Fonte dos dados: https://bolsadeaposta.bet.br (API pública da exchange).
"""
import argparse
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import quant_model as qm  # núcleo quant N3 (Poisson Dixon-Coles, EV, Kelly, AIF)

# ----------------------------------------------------------------------------
# Configuração
# ----------------------------------------------------------------------------
API_URL = "https://mexchange-api.bolsadeaposta.bet.br/api/events"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={date}"
ESPN_SCHEDULE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/teams/{team_id}/schedule?dates={season}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
PER_PAGE = 100
MAX_PAGES = 4
MARKET_TYPES = "one_x_two,money_line"
MARKET_NAMES = "Match Odds,Moneyline"

DIR = Path(__file__).resolve().parent
DATA_DIR = DIR / "data"

DIAS_SEMANA = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]

# Apelidos curtos para caber na tabela
ALIASES = {
    "athlético paranaense": "Athletico-PR",
    "athletico paranaense": "Athletico-PR",
    "botafogo fr": "Botafogo",
    "botafogo sp": "Botafogo-SP",
    "ec juventude": "Juventude",
    "sc internacional": "Internacional",
    "esporte clube bahia": "Bahia",
    "mirassol fc": "Mirassol",
    "atlético goianiense": "Atlético-GO",
    "cr flamengo": "Flamengo",
    "santos fc": "Santos",
    "são paulo fc": "São Paulo",
    "sc corinthians paulista": "Corinthians",
    "sport club corinthians": "Corinthians",
    "se palmeiras": "Palmeiras",
    "clube de regatas vasco da gama": "Vasco",
    "grêmio fbpa": "Grêmio",
    "red bull bragantino": "Bragantino",
    "rb bragantino": "Bragantino",
    "cuiabá ec": "Cuiabá",
    "fortaleza ec": "Fortaleza",
    "ec vitória": "Vitória",
    "sport club recife": "Sport",
    "sport recife": "Sport",
    "juventude": "Juventude",
}

# Clubes brasileiros (normalizados: minúsculas, sem acentos) para casar jogos
# internacionais (Libertadores, Sul-Americana, etc. — onde country != brazil).
# Casa o nome do participante normalizado contra estas chaves.
CLUBES_BR = {
    "flamengo", "cr flamengo", "palmeiras", "se palmeiras", "corinthians",
    "sport club corinthians paulista", "sc corinthians paulista", "sao paulo", "sao paulo fc",
    "santos", "santos fc", "vasco da gama", "clube de regatas vasco da gama",
    "botafogo", "botafogo fr", "ec juventude", "juventude", "sc internacional", "internacional",
    "esporte clube bahia", "bahia", "mirassol", "mirassol fc", "atletico goianiense", "atletico go",
    "gremio", "gremio fbpa", "red bull bragantino", "rb bragantino", "bragantino", "cuiaba",
    "cuiaba ec", "fortaleza", "fortaleza ec", "ec vitoria", "vitoria",
    "sport club recife", "sport recife", "sport", "athletico paranaense", "athletico-pr",
    "cruzeiro", "fluminense", "atletico mineiro", "atletico-mg", "coritiba",
    "chapecoense", "ceara", "ceara sc", "america mineiro", "america-mg", "goias",
    "ponte preta", "operario", "novorizontino", "avai", "avai fc", "parana",
    "sampaio correa", "paysandu",
}

def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos (ASCII)."""
    from unicodedata import normalize as _norm
    t = _norm("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return t.lower().strip()

# Chaves de clube normalizadas (sem acento) para comparação
_CLUBES_BR_NORM = {normalizar(c) for c in CLUBES_BR}

def eh_time_brasileiro(nome: str) -> bool:
    """True se o participante é um clube brasileiro conhecido (nome exato).

    Casa pelo nome normalizado completo (ex.: 'santos fc', 'vasco da gama',
    'palmeiras') para evitar colisão com times estrangeiros homônimos.
    Variantes curtas/abreviadas estão no próprio CLUBES_BR.
    """
    n = normalizar(nome)
    return n in _CLUBES_BR_NORM

# Rótulo curto por competição (via url-name da meta-tag COMPETITION)
COMPETICAO_LABEL = {
    "brazil-serie-a": "Série A",
    "brazil-serie-b": "Série B",
    "brazil-serie-c": "Série C",
    "brazil-serie-d": "Série D",
    "brazil-cup": "Copa do Brasil",
    "copa-do-brasil": "Copa do Brasil",
    "brazil-nordeste-cup": "Copa do Nordeste",
    "copa-nordeste": "Copa do Nordeste",
    "libertadores": "Libertadores",
    "conmebol-libertadores": "Libertadores",
    "copa-libertadores": "Libertadores",
    "sudamericana": "Sul-Americana",
    "conmebol-sudamericana": "Sul-Americana",
    "copa-sudamericana": "Sul-Americana",
}

def rotulo_competicao(evento: dict) -> str:
    """Rótulo curto da competição do evento, ex: 'Série A', 'Copa do Brasil'."""
    for tag in evento.get("meta-tags", []):
        if tag.get("type") == "COMPETITION":
            url = tag.get("url-name", "")
            if url in COMPETICAO_LABEL:
                return COMPETICAO_LABEL[url]
            return url.replace("-", " ").title()
    return "Futebol"


def utc_to_brt(dt_utc: datetime) -> datetime:
    """Converte datetime UTC para horário de Brasília (UTC-3 fixo, sem DST)."""
    return dt_utc.astimezone(timezone(timedelta(hours=-3)))


def carregar_env():
    """Carrega variáveis de .env locais e do Hermes (sem sobrescrever ambiente)."""
    candidatos = [
        DIR / ".env",
        Path.home() / "AppData" / "Local" / "hermes" / ".env",
    ]
    for caminho in candidatos:
        if not caminho.exists():
            continue
        try:
            for linha in caminho.read_text(encoding="utf-8").splitlines():
                linha = linha.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                chave, _, valor = linha.partition("=")
                chave, valor = chave.strip(), valor.strip().strip('"').strip("'")
                if chave and valor and chave not in os.environ:
                    os.environ.setdefault(chave, valor)
        except OSError:
            pass


def http_get_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_get_espn_json(url: str, timeout: int = 30):
    """A API pública ESPN rejeita o user-agent do site da exchange em alguns endpoints."""
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def nomes_compativeis(a: str, b: str) -> bool:
    """Match conservador para provedores: nome completo ou alias conhecido."""
    aliases = {
        "atletica ponte preta": "ponte preta", "operario ferroviario": "operario pr",
        "clube nautico capibaribe": "nautico", "sport club do recife": "sport",
        "sc internacional": "internacional", "ec vitoria": "vitoria",
        "red bull bragantino": "red bull bragantino", "clube do remo": "remo",
    }
    na, nb = normalizar(a), normalizar(b)
    return aliases.get(na, na) == aliases.get(nb, nb)


# ----------------------------------------------------------------------------
# Coleta
# ----------------------------------------------------------------------------
def buscar_eventos() -> list:
    """Busca eventos de futebol abertos (paginação simples).

    A API reporta `total` truncado (=100 por página) mesmo quando existe
    mais de uma página, então NÃO se deve parar por `offset >= total`.
    Para só quando uma página vier vazia (fim real do feed).
    """
    eventos, offset, pagina = [], 0, 0
    while pagina < MAX_PAGES:
        params = urllib.parse.urlencode({
            "offset": offset,
            "per-page": PER_PAGE,
            "sort-by": "start",
            "sort-direction": "asc",
            "sport-ids": "15",  # futebol
            "market-types": MARKET_TYPES,
            "en-market-names": MARKET_NAMES,
            "markets-limit": "30",
        })
        dados = http_get_json(f"{API_URL}?{params}")
        lote = dados.get("events", [])
        eventos.extend(lote)
        offset += PER_PAGE
        pagina += 1
        if not lote or len(lote) < PER_PAGE:
            break  # página incompleta = fim do feed
    return eventos


def filtrar_times_brasileiros(eventos: list) -> list:
    """Mantém eventos de futebol de times brasileiros.

    Inclui:
    - qualquer competição com country == brazil (Série A/B/C/D, Copa do Brasil,
      Copa do Nordeste, estaduais etc.);
    - jogos internacionais cujo algum participante é um clube brasileiro
      reconhecido (Libertadores, Sul-Americana...).
    """
    saida = []
    for ev in eventos:
        # country == brazil cobre todas as competições domésticas
        if any(t.get("type") == "COUNTRY" and t.get("url-name") == "brazil"
               for t in ev.get("meta-tags", [])):
            saida.append(ev)
            continue
        # internacionais: casa participantes com clubes brasileiros conhecidos
        participantes = ev.get("event-participants", [])
        if any(eh_time_brasileiro(p.get("participant-name", "")) for p in participantes):
            saida.append(ev)
    return saida


def jogos_do_dia(eventos: list, agora_brt: datetime) -> list:
    """Mantém jogos com início entre 00:00 e 23:59 (horário de Brasília) de hoje."""
    inicio = agora_brt.replace(hour=0, minute=0, second=0, microsecond=0)
    fim = inicio + timedelta(days=1)
    jogos = []
    for ev in eventos:
        start_utc = datetime.fromisoformat(ev["start"].replace("Z", "+00:00"))
        start_brt = utc_to_brt(start_utc)
        if inicio <= start_brt < fim:
            ev["_start_brt"] = start_brt
            jogos.append(ev)
    jogos.sort(key=lambda e: e["_start_brt"])
    return jogos


def jogos_da_semana(eventos: list, agora_brt: datetime) -> list:
    """Mantém jogos entre agora e os próximos sete dias, em horário de Brasília."""
    fim = agora_brt + timedelta(days=7)
    jogos = []
    for ev in eventos:
        start_utc = datetime.fromisoformat(ev["start"].replace("Z", "+00:00"))
        start_brt = utc_to_brt(start_utc)
        if agora_brt <= start_brt < fim:
            ev["_start_brt"] = start_brt
            jogos.append(ev)
    jogos.sort(key=lambda e: e["_start_brt"])
    return jogos


def proximo_jogo(eventos: list, agora_brt: datetime):
    """Primeiro jogo futuro (para dias sem partida)."""
    futuros = []
    for ev in eventos:
        start_utc = datetime.fromisoformat(ev["start"].replace("Z", "+00:00"))
        start_brt = utc_to_brt(start_utc)
        if start_brt > agora_brt:
            ev["_start_brt"] = start_brt
            futuros.append(ev)
    futuros.sort(key=lambda e: e["_start_brt"])
    return futuros[0] if futuros else None


# ----------------------------------------------------------------------------
# Odds
# ----------------------------------------------------------------------------
def melhor_preco(runner: dict, lado: str):
    """Melhor preço disponível de um runner ('back' = maior odd, 'lay' = menor)."""
    precos = [p["odds"] for p in runner.get("prices", []) if p.get("side") == lado]
    if not precos:
        return None
    return max(precos) if lado == "back" else min(precos)


def extrair_odds(evento: dict):
    """Extrai odds 1X2 do mercado 'Match Odds'. Retorna lista [(nome, back, lay)]."""
    mercado = next(
        (m for m in evento.get("markets", [])
         if m.get("market-type") == "one_x_two" or m.get("name") in ("Match Odds", "Moneyline")),
        None,
    )
    if not mercado:
        return None
    linhas, tem_empate = [], False
    for runner in mercado.get("runners", []):
        nome = runner.get("name", "?")
        if nome.strip().lower() in ("draw", "empate"):
            tem_empate = True
        linhas.append((nome, melhor_preco(runner, "back"), melhor_preco(runner, "lay")))
    # ordena: casa, empate, fora (empate sempre ao meio quando existe)
    if tem_empate and len(linhas) == 3:
        linhas.sort(key=lambda l: (l[0].strip().lower() in ("draw", "empate"),))
        # após sort estável: não-empates mantêm ordem original, empate vai pro fim;
        # reordena casa/empate/fora
        nao_empate = [l for l in linhas if l[0].strip().lower() not in ("draw", "empate")]
        empate = [l for l in linhas if l[0].strip().lower() in ("draw", "empate")]
        linhas = [nao_empate[0], empate[0], nao_empate[1]]
    return linhas


def encurtar(nome: str, largura: int = 15) -> str:
    limpo = ALIASES.get(nome.strip().lower(), nome.strip())
    return limpo[:largura]


def fmt_volume(valor: float) -> str:
    if valor >= 1_000_000:
        return f"R$ {valor / 1_000_000:.1f}M"
    if valor >= 1_000:
        return f"R$ {valor / 1_000:.0f}K"
    return f"R$ {valor:.0f}"


def fmt_odd(odds) -> str:
    return f"{odds:.2f}" if isinstance(odds, (int, float)) else "—"


# -----------------------------------------------------------------------------
# Recomendações: fail-closed. Este bot só divulga uma posição quando a entrada
# do modelo já foi validada e a odd atual foi observada na exchange.
# -----------------------------------------------------------------------------
MIN_EV = 0.05
KELLY_FRACTION = 0.25
MAX_STAKE_PCT = 2.0


def calcular_stake_pct(probabilidade: float, odds: float) -> float:
    """Kelly fracionado (1/4), com teto de 2% da banca por posição."""
    if not isinstance(probabilidade, (int, float)) or not isinstance(odds, (int, float)):
        return 0.0
    if not 0 < probabilidade < 1 or odds <= 1:
        return 0.0
    kelly = (probabilidade * odds - 1) / (odds - 1)
    return round(max(0.0, min(MAX_STAKE_PCT, kelly * KELLY_FRACTION * 100)), 2)


def estimar_1x2_poisson(lambda_home: float, lambda_away: float, max_gols: int = 10) -> dict:
    """Probabilidades 1X2 por distribuição de Poisson truncada e normalizada."""
    if lambda_home <= 0 or lambda_away <= 0:
        raise ValueError("lambdas devem ser positivos")
    home = draw = away = 0.0
    for gols_casa in range(max_gols + 1):
        p_casa = math.exp(-lambda_home) * lambda_home ** gols_casa / math.factorial(gols_casa)
        for gols_fora in range(max_gols + 1):
            p_fora = math.exp(-lambda_away) * lambda_away ** gols_fora / math.factorial(gols_fora)
            p = p_casa * p_fora
            if gols_casa > gols_fora:
                home += p
            elif gols_casa == gols_fora:
                draw += p
            else:
                away += p
    total = home + draw + away
    return {"home": home / total, "draw": draw / total, "away": away / total}


def calcular_recomendacao_modelo(selection: str, odds: float, probability: float,
                                 match: str, source: str) -> dict | None:
    """Cria recomendação somente quando o modelo supera o limiar de EV."""
    ev = probability * odds - 1
    stake = calcular_stake_pct(probability, odds)
    if ev < MIN_EV or stake <= 0:
        return None
    return {
        "match": match,
        "selection": selection,
        "odds": odds,
        "model_probability": probability,
        "ev": ev,
        "stake_pct": stake,
        "price_verified": True,
        "data_status": "verified",
        "source": source,
    }


def _placar_evento_espn(evento: dict, team_id: str):
    """(gols pró, gols contra, homeAway) de um jogo encerrado da ESPN."""
    comp = evento.get("competitions", [{}])[0]
    if comp.get("status", {}).get("type", {}).get("name") != "STATUS_FULL_TIME":
        return None
    cs = comp.get("competitors", [])
    alvo = next((c for c in cs if str(c.get("team", {}).get("id")) == str(team_id)), None)
    rival = next((c for c in cs if c is not alvo), None)
    if not alvo or not rival:
        return None
    try:
        return float(alvo["score"]["value"]), float(rival["score"]["value"]), alvo["homeAway"]
    except (KeyError, TypeError, ValueError):
        return None


def _taxas_time_espn(league: str, team_id: str, season: int):
    dados = http_get_espn_json(ESPN_SCHEDULE_URL.format(league=league, team_id=team_id, season=season))
    mandante, visitante = [], []
    for evento in dados.get("events", []):
        placar = _placar_evento_espn(evento, team_id)
        if not placar:
            continue
        gf, ga, lado = placar
        (mandante if lado == "home" else visitante).append((gf, ga))
    def media(jogos, idx):
        return sum(j[idx] for j in jogos) / len(jogos) if jogos else None
    return {
        "home_gf": media(mandante, 0), "home_ga": media(mandante, 1), "home_n": len(mandante),
        "away_gf": media(visitante, 0), "away_ga": media(visitante, 1), "away_n": len(visitante),
    }


# Constante de shrnkage (empirical-Bayes clássico): quantos jogos "fantasma" de
# média da competição contam a favor da média do time. Amostra curta -> a média
# puxa forte para o baseline da liga; amostra grande -> prevalece a média real.
SHRINKAGE_K = 30
BASELINE_GOL_LIGA = {
    # Médias reais de gols por time por jogo (ataque), coletadas do calendário
    # ESPN 2026 (backtest tmp/coleta_calendario.py): Serie A = 1.329, B = 1.159.
    # C/D não expõem histórico na ESPN -> mantemos 1.30/1.25 como default.
    "bra.1": 1.329, "bra.2": 1.159, "bra.3": 1.20, "bra.4": 1.15,
}


def _com_shrinkage(media_time: float, n: int, baseline: float, k: float = SHRINKAGE_K) -> float | None:
    """Média do time encolhida (empirical Bayes): tempo real + k jogos de baseline.

    n = número de jogos reais observados. Se n == 0 -> baseline (não assume nada
    do time). Reduz o extremo de azarões de odds longas em amostras curtas.
    """
    if media_time is None:
        return baseline
    if n <= 0:
        return baseline
    return (media_time * n + baseline * k) / (n + k)


def _taxas_com_shrinkage(league: str, team_id: str, season: int) -> dict | None:
    """Taxas de gols do time com shrinkage em direção à média da divisão.

    Fail-closed: exige min. 8 jogos reais; senão retorna None (não assume time).
    """
    raw = _taxas_time_espn(league, team_id, season)
    if min(raw["home_n"], raw["away_n"]) < 8:
        return None
    base = BASELINE_GOL_LIGA.get(league, 1.30)
    return {
        "home_gf": _com_shrinkage(raw["home_gf"], raw["home_n"], base),
        "home_ga": _com_shrinkage(raw["home_ga"], raw["home_n"], base),
        "away_gf": _com_shrinkage(raw["away_gf"], raw["away_n"], base),
        "away_ga": _com_shrinkage(raw["away_ga"], raw["away_n"], base),
    }


def _espn_partida_por_nomes(league: str, date: str, casa: str, fora: str):
    dados = http_get_espn_json(ESPN_SCOREBOARD_URL.format(league=league, date=date))
    for evento in dados.get("events", []):
        cs = evento.get("competitions", [{}])[0].get("competitors", [])
        home = next((c for c in cs if c.get("homeAway") == "home"), None)
        away = next((c for c in cs if c.get("homeAway") == "away"), None)
        if home and away and nomes_compativeis(home["team"]["displayName"], casa) and nomes_compativeis(away["team"]["displayName"], fora):
            return home, away
    return None


def gerar_recomendacoes_espn(jogos: list, agora_brt: datetime) -> list:
    """Modelo Poisson inicial para Série B, baseado no histórico 2026 da ESPN.

    Sem xG, escalações ou lesões: o escopo é limitado e qualquer falha exclui
    a partida, nunca cria uma indicação estimada.
    """
    recomendacoes = []
    league = "bra.2"
    date = agora_brt.strftime("%Y%m%d")
    for ev in jogos:
        if rotulo_competicao(ev) != "Série B":
            continue
        participantes = [p.get("participant-name", "") for p in ev.get("event-participants", [])]
        if len(participantes) != 2:
            continue
        casa, fora = participantes
        try:
            partida = _espn_partida_por_nomes(league, date, casa, fora)
            if not partida:
                continue
            home, away = partida
            th = _taxas_time_espn(league, home["team"]["id"], agora_brt.year)
            ta = _taxas_time_espn(league, away["team"]["id"], agora_brt.year)
            if min(th["home_n"], ta["away_n"]) < 8:
                continue
            lambda_home = (th["home_gf"] + ta["away_ga"]) / 2
            lambda_away = (ta["away_gf"] + th["home_ga"]) / 2
            probs = estimar_1x2_poisson(lambda_home, lambda_away)
            odds = extrair_odds(ev) or []
            for nome, back, _ in odds:
                if not back:
                    continue
                chave = "draw" if normalizar(nome) in ("draw", "empate") else (
                    "home" if nomes_compativeis(nome, casa) else "away" if nomes_compativeis(nome, fora) else None
                )
                if chave:
                    rec = calcular_recomendacao_modelo(
                        f"{ev['name']} — {nome}", back, probs[chave], ev["name"],
                        "Resultados 2026 ESPN + Poisson (Série B)",
                    )
                    if rec:
                        recomendacoes.append(rec)
        except Exception as exc:
            print(f"[!] Modelo ESPN ignorou {ev.get('name')}: {exc}")
    return recomendacoes_verificadas(recomendacoes)


# Liga ESPN para cada competição brasileira (Brasileirão A/B/C/D). Competições
# sem histórico ESPN coberto ficam de fora (fail-closed): melhor não recomendar
# do que recomendar com dados ausentes.
COMPETICAO_ESPN_LEAGUE = {
    "Série A": "bra.1",
    "Série B": "bra.2",
    "Série C": "bra.3",
    "Série D": "bra.4",
}


def gerar_recomendacoes_espn_n3(jogos: list, agora_brt: datetime) -> list:
    """Modelo quant N3: Poisson Dixon-Coles nas 4 divisões BR (feedback ESPN).

    Fail-closed: só recomenda quando o histórico da ESPN tem amostra mínima,
    o jogo foi encontrado no scoreboard e a odd Back foi observada na exchange.
    Agent 1 (AIF) e Agent 2 (tático) permanecem avaliados mas — sem fonte de
    xG/escalação na ESPN — não alteram a probabilidade: qualquer desvio de
    contexto seria fabricação, e aqui fabricação não entra.
    """
    recomendacoes = []
    date = agora_brt.strftime("%Y%m%d")
    for ev in jogos:
        comp = rotulo_competicao(ev)
        league = COMPETICAO_ESPN_LEAGUE.get(comp)
        if not league:
            continue  # sem histórico ESPN -> NO BET
        participantes = [p.get("participant-name", "") for p in ev.get("event-participants", [])]
        if len(participantes) != 2:
            continue
        casa, fora = participantes
        try:
            partida = _espn_partida_por_nomes(league, date, casa, fora)
            if not partida:
                continue
            home, away = partida
            th = _taxas_com_shrinkage(league, home["team"]["id"], agora_brt.year)
            ta = _taxas_com_shrinkage(league, away["team"]["id"], agora_brt.year)
            if th is None or ta is None:
                continue
            lambda_home = (th["home_gf"] + ta["away_ga"]) / 2
            lambda_away = (ta["away_gf"] + th["home_ga"]) / 2
            merc = qm.estimar_mercados(lambda_home, lambda_away, rho=0.08)
            odds = extrair_odds(ev) or []
            for nome, back, _ in odds:
                if not back:
                    continue
                chave = "draw" if normalizar(nome) in ("draw", "empate") else (
                    "home" if nomes_compativeis(nome, casa) else "away" if nomes_compativeis(nome, fora) else None
                )
                if chave and chave in ("home", "draw", "away"):
                    rec = qm.montar_recomendacao(
                        odds=back,
                        probabilidade=merc[chave],
                        match=ev["name"],
                        selection=f"{ev['name']} — {nome}",
                        source=f"ESPN {comp} 2026 + Dixon-Coles",
                        commission=float(os.environ.get("BETBOT_COMMISSION", "0") or 0),
                    )
                    if rec:
                        rec["source"] = f"{rec['source']} (1X2)"
                        recomendacoes.append(rec)
        except Exception as exc:
            print(f"[!] Modelo N3 ignorou {ev.get('name')}: {exc}")
    return recomendacoes_verificadas(recomendacoes)


def recomendacoes_verificadas(recomendacoes: list) -> list:
    """Retorna somente recomendações auditáveis e com EV >= 5%."""
    saida = []
    for rec in recomendacoes or []:
        odds = rec.get("odds")
        prob = rec.get("model_probability")
        if not (rec.get("price_verified") and rec.get("data_status") == "verified"):
            continue
        if not isinstance(odds, (int, float)) or not isinstance(prob, (int, float)):
            continue
        ev = prob * odds - 1
        stake = calcular_stake_pct(prob, odds)
        if ev < MIN_EV or stake <= 0:
            continue
        item = dict(rec)
        item["ev"] = ev
        item["stake_pct"] = stake
        saida.append(item)
    return sorted(saida, key=lambda x: (x["ev"], x["stake_pct"]), reverse=True)


def carregar_recomendacoes() -> list:
    """Lê entradas de modelo previamente verificadas; ausência significa NO BET."""
    arquivo = DATA_DIR / "model_recommendations.json"
    if not arquivo.exists():
        return []
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        return dados if isinstance(dados, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def bloco_melhores_apostas(recomendacoes: list) -> str:
    linhas = ["🎯 <b>MELHORES APOSTAS</b>"]
    if not recomendacoes:
        linhas.append("Nenhuma aposta VERIFICADA | 0% da banca")
        linhas.append("<i>Sem modelo independente calibrado e dados verificados, a decisão é NO BET.</i>")
        return "\n".join(linhas)
    for rec in recomendacoes[:5]:
        linhas.append(f"{rec.get('selection', 'Seleção')} @ {fmt_odd(rec['odds'])} | {rec['stake_pct']:.2f}% da banca")
    return "\n".join(linhas)


def bloco_jogos(titulo: str, jogos: list) -> str:
    linhas = [f"🏟 <b>{titulo}</b>"]
    if not jogos:
        linhas.append("Nenhum jogo encontrado no período.")
        return "\n".join(linhas)
    for ev in jogos:
        odds = extrair_odds(ev) or []
        odds_txt = " · ".join(
            f"{('Empate' if nome.strip().lower() in ('draw', 'empate') else encurtar(nome))} {fmt_odd(back)}"
            for nome, back, _ in odds
        ) or "mercado 1X2 indisponível"
        linhas.extend([
            f"\n<b>{ev['name']}</b> · {rotulo_competicao(ev)} · {ev['_start_brt']:%d/%m %H:%M}",
            f"ODDS: {odds_txt}",
            "INFORMAÇÕES / DESFALQUES: <i>não verificados</i>",
        ])
    return "\n".join(linhas)


# ----------------------------------------------------------------------------
# Mensagem
# ----------------------------------------------------------------------------
def montar_mensagem(jogos: list, agora_brt: datetime, proximo=None, weekly_games=None,
                    recomendacoes=None) -> str:
    """Monta o boletim em três blocos; recomendação sem evidência é sempre NO BET."""
    data_hoje = agora_brt.strftime("%d/%m/%Y")
    titulo = (
        f"⚽️ <b>BETINA | FUTEBOL BRASILEIRO</b>\n"
        f"📅 {DIAS_SEMANA[agora_brt.weekday()]}, {data_hoje}\n"
        f"📈 Odds Back da exchange — Bolsa de Aposta\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    if weekly_games is None:
        weekly_games = jogos
    if recomendacoes is None:
        recomendacoes = recomendacoes_verificadas(carregar_recomendacoes())

    partes = [titulo, bloco_melhores_apostas(recomendacoes), bloco_jogos("JOGOS DO DIA", jogos)]
    jogos_semana_futuros = [ev for ev in weekly_games if ev["_start_brt"].date() != agora_brt.date()]
    partes.append(bloco_jogos("JOGOS DA SEMANA", jogos_semana_futuros))
    if not jogos and proximo is not None:
        partes.append(f"⏭ Próximo jogo: <b>{proximo['name']}</b> · {proximo['_start_brt']:%d/%m %H:%M}")
    partes.append(
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Odds e informações podem mudar. Conteúdo informativo +18.\n"
        f"🤖 Gerado às {agora_brt:%H:%M} · fonte de odds: bolsadeaposta.bet.br"
    )
    return "\n\n".join(partes)


# ----------------------------------------------------------------------------
# Boas-vindas
# ----------------------------------------------------------------------------
def montar_boas_vindas(agora_brt: datetime) -> str:
    return (
        "👋 <b>Olá! Eu sou a Betina!</b> ⚽️\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Seja muito bem-vindo(a) à lista VIP do futebol brasileiro! 🇧🇷\n\n"
        "📌 <b>O que eu faço?</b>\n"
        "Todos os dias, 8:30 da manhã, eu te mando:\n"
        "🏟 Os jogos do futebol brasileiro do dia: Brasileirão (A/B/C/D),\n"
        "   Copa do Brasil, estaduais, Libertadores e Sul-Americana\n"
        "🕐 Horário de cada partida (hora de Brasília)\n"
        "📈 As odds da exchange (<b>Back</b> e <b>Lay</b>)\n"
        "💰 O volume negociado em cada jogo\n\n"
        "😴 Não tem jogo? Eu aviso e te conto quando vem o próximo!\n\n"
        "💡 <i>Back = apostar a favor · Lay = apostar contra</i>\n"
        "⚠️ Conteúdo informativo. Aposte com responsabilidade · +18\n\n"
        "🤖 Me manda um oi quando quiser — agora é só aguardar o card de amanhã! 🍀"
    )


def dividir_mensagem(texto: str, limite: int = 4096) -> list:
    """Divide texto longo sem perder caracteres; prefere cortes após quebra de linha."""
    if len(texto) <= limite:
        return [texto]
    partes = []
    restante = texto
    while len(restante) > limite:
        corte = restante.rfind("\n", 0, limite + 1)
        if corte < 1:
            corte = limite
        else:
            corte += 1  # preserva a quebra de linha na parte anterior
        partes.append(restante[:corte])
        restante = restante[corte:]
    partes.append(restante)
    return partes


def enviar_telegram(token: str, chat_id: str, texto: str):
    respostas = []
    for parte in dividir_mensagem(texto):
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": parte,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            respostas.append(json.loads(resp.read().decode("utf-8")))
    return respostas


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Bot diário de jogos de times brasileiros (odds da exchange)")
    parser.add_argument("--envio", action="store_true", help="envia o card diário para os destinatários")
    parser.add_argument("--boas-vindas", nargs="?", const="TODOS", default=None, metavar="CHAT_ID",
                        help="envia mensagem de boas-vindas (todos os destinatários ou um CHAT_ID)")
    parser.add_argument("--token", help="token do bot (sobrepõe config)")
    args = parser.parse_args()

    carregar_env()

    token = args.token or os.environ.get("BETBOT_TELEGRAM_TOKEN")
    chats_cfg = (os.environ.get("BETBOT_CHAT_ID") or "")
    destinatarios = [c.strip() for c in chats_cfg.split(",") if c.strip()]
    if not token or not destinatarios:
        print("[x] Token/destinatários não configurados (.env: BETBOT_TELEGRAM_TOKEN, BETBOT_CHAT_ID)")
        sys.exit(1)

    agora_utc = datetime.now(timezone.utc)
    agora_brt = utc_to_brt(agora_utc)

    # --- modo boas-vindas ---------------------------------------------------
    if args.boas_vindas is not None:
        alvos = destinatarios if args.boas_vindas == "TODOS" else [args.boas_vindas]
        for alvo in alvos:
            try:
                enviar_telegram(token, alvo, montar_boas_vindas(agora_brt))
                print(f"[✓] Boas-vindas enviada para {alvo}")
            except Exception as e:
                print(f"[x] Falha ao enviar para {alvo}: {e}")
                sys.exit(1)
        return

    # --- card diário --------------------------------------------------------
    if not args.envio:
        import html as _html
        print(f"[i] Destinatários: {', '.join(destinatarios)}")
        print(f"[i] Coletando eventos… ({agora_brt:%d/%m/%Y %H:%M} Brasília)")
        eventos = buscar_eventos()
        br = filtrar_times_brasileiros(eventos)
        jogos = jogos_do_dia(br, agora_brt)
        semana = jogos_da_semana(br, agora_brt)
        recomendacoes = gerar_recomendacoes_espn_n3(jogos, agora_brt) + recomendacoes_verificadas(carregar_recomendacoes())
        recomendacoes = recomendacoes_verificadas(recomendacoes)
        print(f"[i] {len(eventos)} eventos · {len(br)} BR · {len(jogos)} hoje · {len(semana)} na semana · {len(recomendacoes)} value")
        mensagem = montar_mensagem(
            jogos, agora_brt, proximo_jogo(br, agora_brt), weekly_games=semana,
            recomendacoes=recomendacoes,
        )
        print("\n--- MENSAGEM (HTML) ---\n" + mensagem)
        print("\n--- VISUALIZAÇÃO ---")
        print(_html.unescape(mensagem).replace("<b>", "").replace("</b>", "")
              .replace("<pre>", "").replace("</pre>", ""))
        return

    print(f"[i] Coletando eventos… ({agora_brt:%d/%m/%Y %H:%M} Brasília)")
    eventos = buscar_eventos()
    br = filtrar_times_brasileiros(eventos)
    jogos = jogos_do_dia(br, agora_brt)
    semana = jogos_da_semana(br, agora_brt)
    recomendacoes = gerar_recomendacoes_espn_n3(jogos, agora_brt) + recomendacoes_verificadas(carregar_recomendacoes())
    recomendacoes = recomendacoes_verificadas(recomendacoes)
    print(f"[i] {len(eventos)} eventos · {len(br)} BR · {len(jogos)} hoje · {len(semana)} na semana · {len(recomendacoes)} value")

    mensagem = montar_mensagem(
        jogos, agora_brt, proximo_jogo(br, agora_brt), weekly_games=semana,
        recomendacoes=recomendacoes,
    )

    DATA_DIR.mkdir(exist_ok=True)
    snapshot = {
        "gerado_em": agora_utc.isoformat(),
        "jogos_hoje": [
            {
                "nome": ev["name"],
                "inicio_brt": ev["_start_brt"].isoformat(),
                "volume": ev.get("volume"),
                "odds": extrair_odds(ev),
            }
            for ev in jogos
        ],
        "jogos_semana": [
            {"nome": ev["name"], "inicio_brt": ev["_start_brt"].isoformat(), "odds": extrair_odds(ev)}
            for ev in semana
        ],
        "recomendacoes_verificadas": recomendacoes,
    }
    (DATA_DIR / f"snapshot_{agora_brt:%Y-%m-%d}.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ok_todos = True
    for chat in destinatarios:
        try:
            respostas = enviar_telegram(token, chat, mensagem)
        except Exception as e:
            ok_todos = False
            print(f"[x] Falha ao enviar para {chat}: {e}")
            continue
        if isinstance(respostas, dict):
            respostas = [respostas]
        ok = all(r.get("ok") for r in respostas)
        ok_todos = ok_todos and ok
        print(f"[{'✓' if ok else 'x'}] Envio para {chat} {'ok' if ok else 'FALHOU'}")
    if not ok_todos:
        sys.exit(1)


if __name__ == "__main__":
    main()
