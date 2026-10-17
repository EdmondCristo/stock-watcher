# Stock Watcher 📈🧼

Free, hands-off stock alerts using **EMA 10** and **Bollinger Bands**, with news-aware
probabilities, an **AI analyst** that double-checks every alert, and a weekly **AI consultant**
that hunts for durable "people still need soap" stocks. Runs on GitHub Actions for $0.

> Educational tool — not financial advice. Probabilities are estimates, not guarantees.

## What it does

| # | Feature | How |
|---|---------|-----|
| 1 | Email alert when price hits the **top or bottom Bollinger Band** or **crosses below EMA 10** | `src/signals.py` → Gmail SMTP |
| 2 | Runs automatically in the cloud, free | GitHub Actions, every 30 min during market hours |
| 3 | Pulls latest news and estimates **P(rise) vs P(fall)** — e.g. "at lower band, but news is negative → 68% chance it keeps falling" | `src/news.py` + `src/probability.py` |
| 4 | **Analyst agent** re-reads the news, checks sample size and logic, and returns AGREE / DISAGREE / UNCERTAIN | `AnalystAgent` in `src/agents.py` |
| 5 | **Consultant agent**: weekly screen of essentials businesses (household goods, food, discount retail, healthcare, utilities, waste) → top picks memo | `ConsultantAgent` in `src/agents.py` |

### How the probability works
1. **History**: every past time *this* stock hit the same condition (5 years), did it close higher
   N days later? (default N = 5). Gives a base rate and sample size.
2. **News**: headlines are scored −1 … +1 (VADER automatically; the AI analyst then gives its own
   reading). The score shifts the base rate in log-odds. Strength is `news_weight` in `config.yaml`.
3. If P(fall) ≥ 60% at the lower band you get a **falling-knife warning** instead of a buy hint.

## Setup (≈15 minutes, one time)

### 1. Gmail App Password
Gmail won't accept your normal password from a script.
1. Turn on **2-Step Verification**: <https://myaccount.google.com/security>
2. Create an **App Password**: <https://myaccount.google.com/apppasswords> → name it "Stock Watcher" → copy the 16-character code.

### 2. Free AI key (optional but recommended)
- **Gemini** (default): <https://aistudio.google.com/apikey> → *Create API key*. The free tier easily
  covers this program's volume. Note: on the free tier Google may use prompts to improve its models —
  fine here, since only public stock data and headlines are sent.
- Or **Groq**: <https://console.groq.com/keys>, then set `llm.provider: groq` in `config.yaml`.
- No key? Everything still works with rule-based analyst/consultant logic.

### 3. Put the code on GitHub
1. Create a free account at <https://github.com> → **New repository** → name `stock-watcher`.
   - *Public* = unlimited free Actions minutes. *Private* = uses your monthly free allowance; this
     program needs roughly 350–700 min/month, which fits the Free plan.
2. Upload this folder's contents (**Add file → Upload files**, drag everything in, including the
   `.github` folder — on Mac press `Cmd+Shift+.` in Finder to see it). Commit.

### 4. Add your secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|------|-------|
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-character app password |
| `GEMINI_API_KEY` | from step 2 (optional) |
| `GROQ_API_KEY` | only if using Groq |

Secrets are encrypted and never visible in logs, even in a public repo.

### 5. Turn it on & test
1. **Actions** tab → enable workflows if prompted.
2. Click **Stock watch (market hours)** → **Run workflow** (force = ✓). Within ~2 minutes you get
   an email if any watchlist stock has a signal; otherwise the log shows "No new signals".
3. Click **Weekly consultant memo** → **Run workflow** to get your first memo now.

That's it — it runs on its own from here.

## Retirement planner (Sunday plan → Friday buy)

Every **Sunday** three AIs build your plan for the coming **Friday**:
1. **Researcher** gathers facts: prices, fundamentals, news, earnings and dividend dates.
2. **Strategist** proposes the buy plan (core index funds + one essentials stock) and one bigger idea.
3. **Judge** checks every suggestion and marks it ✅ OK, ⚠️ risky, or ❌ wrong. Hard safety rules
   (budget, max 5% per stock, no buying right before earnings) always win, even over the AIs.

Before them, a 4th AI — the **Reviewer** — grades last week's plan, checks whether the Judge's
rejections helped or hurt, totals what you'd have if you followed the Judge every week, projects that
to retirement (low / middle / high, plus in today's dollars), and sends written feedback that the
other three read and the Judge replies to.

A **Manager AI** then hands out the week's tasks (a focus for each AI, extra news checks), the
**Strategist and Judge debate** (up to 4 rounds; 5–10 only if the Judge says it's close, problems are
shrinking, and the Manager approves), and the Manager writes the summary at the top of the email.
The Reviewer also keeps **team memory** (`state/history/team_memory.json`): long-term lessons every AI
reads each week until the Reviewer retires them.

Everything is saved in `state/history/`: one `…-plan.json` and a viewable `…-report.html` per week,
`scorecard.csv` (every purchase), and `lessons.json` (the Reviewer's feedback).

**Friday ~7:45 AM ET** you get a short go / wait email. If the stock jumped out of its buy range
or bad news came out, its money moves into the US index fund instead.

Edit `portfolio.yaml` for your weekly budget and (optionally) your current mix **in percentages**.
No dollar balances or account numbers are ever needed. Keep the repo **private**.

## Customise
Everything lives in `config.yaml`: watchlist, EMA/BB periods, band tolerance, probability horizon,
alert threshold, cooldown, consultant universe. Edit on GitHub (pencil icon) and commit.

## Run locally (optional)
```bash
pip install -r requirements.txt
python main.py watch --demo          # offline test with fake data — prints the email
python main.py watch --force --dry-run
python main.py consult --dry-run
python -m pytest
```

## Good to know
- **Data**: Yahoo Finance via `yfinance` (free, ~15-min delay intraday). Yahoo occasionally changes
  things; if data stops loading, `pip install -U yfinance` usually fixes it (the workflow installs the latest every run).
- **No spam**: the same alert for the same stock is sent at most once per `cooldown_hours`. Below-EMA
  alerts only fire on a fresh cross, not every day of a downtrend.
- **Stays alive**: GitHub pauses schedules in public repos after 60 days of no activity; the weekly
  consultant commits a heartbeat file so that doesn't happen.
- **Cron timing**: GitHub may delay scheduled runs by a few minutes at busy times — fine for this use.
