# ⚽️ Brasileirão Odds Bot

Bot que **todo dia de manhã** busca os jogos do **Brasileirão Série A** na bolsa de apostas
[Bolsa de Aposta Exchange](https://bolsadeaposta.bet.br/b/exchange) e envia um card caprichado
no **Telegram** com horários e odds (**Back/Lay**).

> Feito com ❤️ e ZERO dependências — só biblioteca padrão do Python 3.9+.

---

## 📱 Exemplo da mensagem

```
⚽️ BRASILEIRÃO SÉRIE A
📅 Segunda, 24/08/2026
📈 Odds da exchange — Bolsa de Aposta
━━━━━━━━━━━━━━━━━━━━━━

🏟 Botafogo FR vs Athlético Paranaense 🔴 AO VIVO
🕐 20:01 (Brasília) · 💰 R$ 4.0M

                    Back    Lay
Botafogo            410.00  —
Empate              220.00  340.00
Athletico-PR        —       1.01

━━━━━━━━━━━━━━━━━━━━━━
💡 Back = apostar a favor · Lay = contra
🤖 Gerado às 21:49 · fonte: bolsadeaposta.bet.br
```

Em dias sem jogos do Brasileirão, o bot avisa e mostra o **próximo confronto**.

## 🚀 Como funciona

1. Consulta a API pública da exchange (`mexchange-api.bolsadeaposta.bet.br/api/events`)
2. Filtra os eventos cuja competição é `Brazil Serie A`
3. Mantém apenas os jogos com início **hoje** (fuso de Brasília, UTC−3)
4. Extrai as melhores odds de **Match Odds** (mercado 1X2): maior odd de *back*,
   menor de *lay*
5. Monta a mensagem em HTML e envia via `sendMessage` do Telegram
6. Salva um snapshot em `data/snapshot_YYYY-MM-DD.json` para histórico

## 📦 Instalação

```bash
git clone https://github.com/SEU_USUARIO/brasileirao-odds-bot.git
cd brasileirao-odds-bot
```

Configure as credenciais criando um arquivo `.env` ao lado do script
(**ou** apenas exporte as variáveis de ambiente):

```ini
BETBOT_TELEGRAM_TOKEN=123456:ABC-SEU-TOKEN-DO-BOT
BETBOT_CHAT_ID=-1001234567890
```

> 💡 Dica: crie o bot com o [@BotFather](https://t.me/BotFather).
> Para descobrir o `chat_id`, mande uma mensagem pro bot e abra
> `https://api.telegram.org/bot<TOKEN>/getUpdates`.

## ▶️ Uso

```bash
python brasileirao_bot.py            # dry-run: mostra a mensagem no terminal
python brasileirao_bot.py --envio    # envia para o Telegram
```

## ⏰ Agendamento diário (8:30)

### Linux / macOS (cron)

```cron
30 8 * * * cd /caminho/ate/o/projeto && /usr/bin/python3 brasileirao_bot.py --envio >> bot.log 2>&1
```

### Windows (Agendador de Tarefas)

```powershell
schtasks /Create /TN "BrasileiraoOddsBot" /SC DAILY /ST 08:30 ^
  /TR "python C:\caminho\ate\o\projeto\brasileirao_bot.py --envio"
```

## 🗂 Estrutura

| Arquivo                | Função                                    |
|------------------------|-------------------------------------------|
| `brasileirao_bot.py`   | Bot completo (coleta + formatação + envio)|
| `.env`                 | Credenciais (**nunca commitado**)         |
| `data/`                | Snapshots JSON por dia (**nunca commitado**)|

## ⚠️ Aviso

Projeto informativo/educacional. As odds são capturadas da bolsa em tempo quase real
e podem mudar a qualquer momento. Aposte com responsabilidade — +18.
