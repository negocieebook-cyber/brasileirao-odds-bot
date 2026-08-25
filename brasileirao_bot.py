#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚽ Brasileirão Odds Bot
======================
Busca os jogos do Brasileirão Série A do dia na bolsa de apostas
(Bolsa de Aposta / exchange) com as odds de Match Odds (1X2),
e envia um resumo formatado para o Telegram.

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
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ----------------------------------------------------------------------------
# Configuração
# ----------------------------------------------------------------------------
API_URL = "https://mexchange-api.bolsadeaposta.bet.br/api/events"
SERIE_A_URL_NAME = "brazil-serie-a"
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


# ----------------------------------------------------------------------------
# Coleta
# ----------------------------------------------------------------------------
def buscar_eventos() -> list:
    """Busca eventos de futebol abertos (paginação simples)."""
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
        total = int(dados.get("total", len(eventos)))
        offset += PER_PAGE
        pagina += 1
        if not lote or offset >= total:
            break
    return eventos


def filtrar_serie_a(eventos: list) -> list:
    """Mantém apenas eventos cujo meta-tag de competição é Brazil Serie A."""
    saida = []
    for ev in eventos:
        for tag in ev.get("meta-tags", []):
            if tag.get("type") == "COMPETITION" and tag.get("url-name") == SERIE_A_URL_NAME:
                saida.append(ev)
                break
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


# ----------------------------------------------------------------------------
# Mensagem
# ----------------------------------------------------------------------------
def montar_mensagem(jogos: list, agora_brt: datetime, proximo=None) -> str:
    data_hoje = agora_brt.strftime("%d/%m/%Y")
    titulo = (
        f"⚽️ <b>BRASILEIRÃO SÉRIE A</b>\n"
        f"📅 {DIAS_SEMANA[agora_brt.weekday()]}, {data_hoje}\n"
        f"📈 Odds da exchange — Bolsa de Aposta\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    if not jogos:
        partes = [titulo, "\n😴 <b>Nenhum jogo do Brasileirão hoje.</b>"]
        if proximo is not None:
            p_start = proximo["_start_brt"]
            quando = p_start.strftime("%d/%m (%a) às %H:%M").replace(
                p_start.strftime("%a"), DIAS_SEMANA[p_start.weekday()]
            )
            partes.append(f"\n⏭ Próximo: <b>{proximo['name']}</b>\n🗓 {quando} (Brasília)")
        partes.append(f"\n🤖 Gerado às {agora_brt:%H:%M} · bolsadeaposta.bet.br")
        return "\n".join(partes)

    blocos = []
    for ev in jogos:
        ao_vivo = bool(ev.get("in-running-flag")) or any(
            m.get("live") for m in ev.get("markets", [])
        )
        selo = " 🔴 <b>AO VIVO</b>" if ao_vivo else ""
        cabecalho = (
            f"\n🏟 <b>{ev['name']}</b>{selo}\n"
            f"🕐 {ev['_start_brt']:%H:%M} (Brasília) · 💰 {fmt_volume(ev.get('volume', 0))}"
        )
        linhas_odds = extrair_odds(ev)
        if linhas_odds:
            tabela = ["<pre>", f"{'':15s} Back    Lay"]
            for nome, back, lay in linhas_odds:
                rotulo = "Empate" if nome.strip().lower() in ("draw", "empate") else encurtar(nome)
                tabela.append(f"{rotulo:15s} {fmt_odd(back):6s} {fmt_odd(lay)}")
            tabela.append("</pre>")
            bloco = cabecalho + "\n" + "\n".join(tabela)
        else:
            bloco = cabecalho
        blocos.append(bloco)

    rodape = (
        f"\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 Back = apostar a favor · Lay = contra\n"
        f"🤖 Gerado às {agora_brt:%H:%M} · fonte: bolsadeaposta.bet.br"
    )
    return titulo + "\n" + "\n".join(blocos) + "\n" + rodape


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
        "🏟 Os jogos do <b>Brasileirão Série A</b> do dia\n"
        "🕐 Horário de cada partida (hora de Brasília)\n"
        "📈 As odds da exchange (<b>Back</b> e <b>Lay</b>)\n"
        "💰 O volume negociado em cada jogo\n\n"
        "😴 Não tem jogo? Eu aviso e te conto quando vem o próximo!\n\n"
        "💡 <i>Back = apostar a favor · Lay = apostar contra</i>\n"
        "⚠️ Conteúdo informativo. Aposte com responsabilidade · +18\n\n"
        "🤖 Me manda um oi quando quiser — agora é só aguardar o card de amanhã! 🍀"
    )


def enviar_telegram(token: str, chat_id: str, texto: str):
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Bot diário do Brasileirão Série A (odds da exchange)")
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
        serie_a = filtrar_serie_a(eventos)
        jogos = jogos_do_dia(serie_a, agora_brt)
        print(f"[i] {len(eventos)} eventos · {len(serie_a)} Série A · {len(jogos)} hoje")
        mensagem = montar_mensagem(jogos, agora_brt, proximo_jogo(serie_a, agora_brt))
        print("\n--- MENSAGEM (HTML) ---\n" + mensagem)
        print("\n--- VISUALIZAÇÃO ---")
        print(_html.unescape(mensagem).replace("<b>", "").replace("</b>", "")
              .replace("<pre>", "").replace("</pre>", ""))
        return

    print(f"[i] Coletando eventos… ({agora_brt:%d/%m/%Y %H:%M} Brasília)")
    eventos = buscar_eventos()
    serie_a = filtrar_serie_a(eventos)
    jogos = jogos_do_dia(serie_a, agora_brt)
    print(f"[i] {len(eventos)} eventos · {len(serie_a)} Série A · {len(jogos)} hoje")

    mensagem = montar_mensagem(jogos, agora_brt, proximo_jogo(serie_a, agora_brt))

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
