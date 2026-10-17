# Stock Watcher 📈🧼

A free, hands-off investing assistant that runs on GitHub Actions for $0. It does two things:

1. **Stock alerts (weekdays):** emails you when a stock on your watchlist touches the top or bottom
   **Bollinger Band** or crosses below its **EMA 10**, with a news-aware probability and an AI
   analyst's second opinion.
2. **Retirement planner (Sunday plan → Friday buy):** a team of **5 AIs** builds your weekly buy plan
   for a 24-year retirement goal, checks it, debates it, and grades itself the following week.

> Educational tool, not financial advice. Probabilities and projections are estimates, not guarantees.
> The program never buys or sells anything — you read the email and place trades yourself.

---

## What you get by email

| When | Email | What's in it |
|------|-------|--------------|
| Weekdays, every 30 min during market hours | 📈 **Stock alert** | Only when a watchlist stock hits a band or crosses below EMA 10. Probability it rises or falls, news, analyst verdict. |
| **Sunday** ~9:20 AM ET | 🧼 **Retirement plan** | Manager's summary, last week graded, 24-year projection, what to buy Friday and how much, the debate, team memory. |
| **Friday** ~7:45 AM ET | ✅ **Buy-day check** | Go / wait for each item, right before the market opens. |

---

## The retirement AI team

Each Sunday the team works in this order:

| # | AI | Job |
|---|----|-----|
| 1 | **Reviewer** (coach) | Grades last week's plan, checks whether the Judge's rejections helped or hurt, adds up what you'd have if you followed the Judge every week, projects that to retirement, and updates the **team memory**. |
| 2 | **Manager AI** | Reads the Reviewer's report and hands out tasks: one focus for each AI, plus extra news checks on up to 3 stocks. Approves or refuses extra debate rounds. Writes the summary at the top of the email. |
| 3 | **Researcher** | Gathers facts: prices, fundamentals, news summaries, red flags, earnings and dividend dates before buy day. |
| 4 | **Strategist** | Creative: proposes Friday's buy plan (core index funds + one "people still need soap" stock) and one bigger idea. |
| 5 | **Judge** | Strict QA: marks each item ✅ OK, ⚠️ risky or ❌ wrong, replies to the Reviewer's feedback, and approves the final plan. |

### The Strategist ⇄ Judge debate (critic loop)
- The Strategist proposes → the Judge critiques → the Strategist fixes only what was flagged → repeat.
- **Normal limit: 4 rounds.**
- **Rounds 5–10 only if absolutely necessary:** the Judge says they're close, **and** the number of
  problems went down last round, **and** the Manager approves. Extensions come 2 rounds at a time.
- **10 is a hard cap.** If they still disagree, the safe rules-only plan fills the gaps.

### Team memory (coach with memory)
- Weekly notes fade; **long-term lessons stay** in `state/history/team_memory.json`.
- The Reviewer adds a lesson only when a problem shows up twice or one serious mistake happens,
  confirms lessons that prove true again, and retires outdated ones (max 12).
- Every AI reads the lessons addressed to it, every week.

### Safety rules no AI can override
Checked by plain code every round of the debate:
- the plan spends exactly your weekly budget
- no single stock above **5%** of your portfolio
- no individual stock bought right before its **earnings report**
- no stock the Researcher red-flagged, or one stretched at the top Bollinger Band
- index funds are bought every week, never "timed"

### How the plan is built
- **Core (~85%)**: `VTI` (US stocks), `VXUS` (international), `BND` (bonds).
- **Satellite (~15%)**: one essentials stock from the list in `config.yaml` → `consultant.universe`.
- **Glide path**: bonds start near 10% and rise toward ~50% at retirement. `retirement_year: 2050`
  counts down automatically, so the plan gets a little safer each January.
- If you fill in your current mix (`holdings`), new money leans toward whatever is under target,
  so you rebalance without ever selling.
- Each satellite pick comes with a **Friday price range**. The Friday check skips it if the price
  jumped out of range or bad news came out; that money goes into `VTI` instead.

### Retirement projection
The Reviewer simulates your weekly amount year by year until retirement, following the glide path,
in three scenarios (low / middle / high), and also shows the result **in today's dollars** after
inflation. Assumptions are at the bottom of `portfolio.yaml` (default ~7% stocks, ~4% bonds,
2.5% inflation). They're educated guesses — change them to see different outcomes.

---

## Setup (≈15 minutes, one time)

### 1. Gmail App Password
Gmail won't accept your normal password from a program.
1. Turn on **2-Step Verification**: <https://myaccount.google.com/security>
2. Create an **App Password**: <https://myaccount.google.com/apppasswords> → name it "Stock Watcher" →
   copy the 16-letter code (Google shows it only once).

### 2. Free AI key (recommended)
Without a key everything still works, but the AIs fall back to basic rules (emails say
"rule-based …") and the debate stops after 1 round. Pick **one**:

- **Groq** (free): <https://console.groq.com/keys> → create a key (starts with `gsk_`).
  In `config.yaml` set `provider: groq`. The program automatically picks a Groq model your free
  account can use (`openai/gpt-oss-120b`, then `openai/gpt-oss-20b`, then `llama-3.1-8b-instant`).
- **Gemini** (free): <https://aistudio.google.com/apikey> → *Create API key*. Newer keys start with
  `AQ.`, older ones with `AIza` — both are fine. In `config.yaml` set `provider: gemini`.
  If you get `401` errors with an `AQ.` key, use Groq instead.

### 3. Put the code on GitHub
1. Create a free account at <https://github.com> → **New repository** → name it `stock-watcher` →
   choose **Private**.
2. **Add file → Upload files** and drag in everything from this folder.
3. The `.github` folder is hidden on most computers and often doesn't upload. If the **Actions** tab
   shows no workflows, create the 4 workflow files by hand: **Add file → Create new file**, type the
   name (e.g. `.github/workflows/watch.yml` — the slashes create the folders), paste the contents
   from this repo's copy, and commit.

When done, the top of the repo should show `main.py`, `config.yaml`, `portfolio.yaml`,
`requirements.txt`, and the `src`, `state` and `tests` folders. `.github/workflows/` should hold
exactly these 4 files:

| File | Shows in Actions as | Schedule |
|------|---------------------|----------|
| `watch.yml` | Stock watch (market hours) | every 30 min, weekdays |
| `consultant.yml` | Weekly consultant memo | Sundays (optional — the retirement plan replaces it) |
| `retirement.yml` | Sunday retirement plan | Sundays ~9:20 AM ET |
| `friday.yml` | Friday buy-day check | Fridays ~7:45 AM ET |

### 4. Add your secrets
Repo → **Settings → Secrets and variables → Actions → Secrets tab → New repository secret**.
Names must match exactly (capitals, underscores):

| Name | Value |
|------|-------|
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-letter app password |
| `GROQ_API_KEY` | your Groq key (if using Groq) |
| `GEMINI_API_KEY` | your Gemini key (if using Gemini) |

Secrets belong to one repo — a new repo needs them added again. They never appear in logs.

### 5. Turn it on and test
1. **Settings → Actions → General → Workflow permissions → Read and write permissions → Save**
   (lets the program save its history).
2. **Actions → Sunday retirement plan → Run workflow.** Run it **before** the Friday check — Friday
   reads the plan Sunday saves.
3. Run it a **second time** to see the Reviewer grade a week (gains show ~0% because both runs used
   the same prices — real numbers start the following week).
4. **Actions → Friday buy-day check → Run workflow.**
5. **Actions → Stock watch (market hours) → Run workflow** (an email arrives only if a stock has a signal).

It runs on its own from here.

---

## What gets saved (`state/history/`)

| File | What it is |
|------|------------|
| `YYYY-MM-DD-plan.json` | Everything from that week: plan, debate, verdicts, review, prices |
| `YYYY-MM-DD-report.html` | The Sunday email as a page (click it on GitHub → **Raw**) |
| `scorecard.csv` | Every planned purchase, one row each (GitHub shows it as a table) |
| `lessons.json` | The Reviewer's weekly feedback |
| `team_memory.json` | Long-term lessons shared with all AIs |

`state/plan.json` holds the current week's plan for the Friday check. `state/alerts.json` stops
repeat stock alerts.

---

## Customise

| To change… | Edit | Setting |
|------------|------|---------|
| Weekly amount ($50–$100) | `portfolio.yaml` | `weekly_budget` |
| Retirement date | `portfolio.yaml` | `retirement_year` |
| Your current mix (percent only, no dollars) | `portfolio.yaml` | `holdings` |
| Index funds used | `portfolio.yaml` | `core_funds` |
| Share for individual stocks | `portfolio.yaml` | `satellite_share`, `max_single_stock` |
| Projection assumptions | `portfolio.yaml` | `projection` |
| Stocks for weekday alerts | `config.yaml` | `watchlist` |
| "Essentials" stocks the team can pick | `config.yaml` | `consultant.universe` |
| AI provider | `config.yaml` | `llm.provider` (`groq`, `gemini`, or `none`) |
| Alert sensitivity | `config.yaml` | `indicators`, `probability`, `alerts` |

On GitHub: click the file → pencil icon → edit → **Commit changes**.

## Privacy
- No dollar balances, account numbers or brokerage logins are ever needed — holdings are percentages.
- The AI only sees stock names, percentages, public prices and headlines.
- Keep the repo **private** (**Settings → General → Danger Zone → Change visibility**).

---

## Troubleshooting

| You see | Cause | Fix |
|---------|-------|-----|
| No workflows in the **Actions** tab | `.github` folder didn't upload | Create the workflow files by hand (Setup step 3) |
| `Invalid workflow file … Unexpected value 'watchlist'` | `config.yaml` contents ended up inside `.github/workflows/` | Delete that file; `config.yaml` belongs at the top of the repo |
| `No module named 'src'` | `src` folder missing | Upload the whole `src` folder (drag the folder itself) |
| `No file matched to **/requirements.txt` | Files missing from this repo | Upload the full project to this repo |
| `GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set` | Gmail secrets missing | Add them (Setup step 4) |
| Log shows `GROQ_API_KEY:` or `GEMINI_API_KEY:` with nothing after it | Secret missing, misnamed, in another repo, or under *Variables* | Re-add under **Actions → Secrets** with the exact name |
| Email says "rule-based …" | AI key not working | Check the run log for `[llm]` lines |
| `[llm] … Invalid leading whitespace … in header value` | Extra space or line break pasted with the key | Fixed automatically now; if it persists, re-paste the key |
| `[llm] HTTP error: 404` (Groq) | Model not offered to your account | Fixed automatically — the program switches to an available model |
| `[llm] HTTP error: 401` (Gemini `AQ.` key) | Some accounts' `AQ.` keys are rejected | Switch to Groq |
| `[llm] HTTP error: 429` | Free AI limit reached for the moment | The program waits and retries; long debates use more requests |
| Failure at "Save alert state" / "Save the plan" | Workflow can't write to the repo | Settings → Actions → General → **Read and write permissions** |
| Yellow notices about Node.js 20 or Ubuntu | GitHub announcing future updates | Safe to ignore |

When a run fails: open it → click the job → open the step with ❌ → copy the last lines.

---

## Run on your own computer (optional)
```bash
pip install -r requirements.txt
python main.py retire --demo       # offline test of the Sunday plan — prints the email
python main.py friday --demo       # offline Friday check
python main.py watch --demo        # offline stock alerts
python main.py retire --dry-run    # real data, prints instead of emailing
python -m pytest                   # automated tests
```

## Project files

| File | Purpose |
|------|---------|
| `main.py` | Entry point: `watch`, `consult`, `retire`, `friday` |
| `src/indicators.py`, `signals.py`, `probability.py` | EMA, Bollinger Bands, signals, odds |
| `src/news.py` | Yahoo + Google News headlines, sentiment |
| `src/agents.py` | Weekday alert analyst and consultant |
| `src/retire.py` | Targets, glide path, weekly allocation, buy ranges, safety rules |
| `src/advisors.py` | Researcher, Strategist, Judge |
| `src/team.py` | Manager AI, debate loop, team memory |
| `src/reviewer.py` | Reviewer: grading, totals, projection |
| `src/history.py` | Saving weekly records, scorecard, lessons |
| `src/retire_run.py` | Runs the Sunday plan and Friday check; builds the emails |
| `src/llm.py` | Talks to Groq or Gemini (auto model fallback, key clean-up, retries) |
| `src/emailer.py` | Sends email through Gmail |

## Good to know
- **Data:** Yahoo Finance via `yfinance` (free, ~15-min delay). The workflows install the latest
  version every run, which usually fixes Yahoo changes.
- **No spam:** each stock alert is sent at most once per `cooldown_hours`; below-EMA alerts fire only
  on a fresh cross.
- **Timing:** GitHub can delay scheduled runs by a few minutes at busy times.
- **Free minutes:** private repos get a monthly allowance; this project fits comfortably.
