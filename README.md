# Monthly investment report — pipeline

A proof of concept that produces a client-ready monthly investment letter from
public data, for a middle-market brokerage client.

> **This is an independent case-study exercise.** It is not affiliated with,
> endorsed by, or produced for XP Investimentos. The client, the advisor and
> the portfolios are fictional; the letters it generates are demonstrations and
> are not investment advice. Market data comes from public CVM, B3 and Banco
> Central sources.

The design rule the whole thing is built around:

> **The language model never produces a number.** It narrates figures the code
> computed, and it may only quote figures that exist in a machine-checked list.
> A letter containing anything else is rejected before a human sees it.

## What it does

1. **Ingests** prices from three public sources: CVM (fund quotas, daily and
   FIDC monthly), B3 via Yahoo (equities and the Ibovespa) and the Brazilian
   Central Bank (CDI, Selic, IPCA).
2. **Computes** the month's return, per-position attribution, look-through
   equity exposure and a declared benchmark — all in code, validated against a
   hand-checked answer key.
3. **Applies** suitability rules read from CSV, not hard-coded, each carrying
   the clause of the client's risk-profile document it comes from.
4. **Sizes** the minimum set of trades that clears every breach, then re-runs
   the rules on the post-trade portfolio to prove the plan works.
5. **Writes** the letter with a language model that receives a list of
   authorised figures and is told to quote them verbatim.
6. **Verifies** every number, the client's name, forbidden language and length.
   Failures are fed back and the model tries again.
7. **Renders** a fixed two-page PDF from a deterministic template, refusing to
   publish if the content does not fit the declared layout budget.

## Requirements

- Python 3.13 (tested on 3.13.14)
- An OpenAI API key. The app looks for it in `.streamlit/secrets.toml` (see
  `.streamlit/secrets.toml.example`), then in the `OPENAI_API_KEY` environment
  variable, and only asks in the sidebar if neither is set. On Streamlit
  Community Cloud, put it in Settings > Secrets. `MODEL_LETTER` sets the drafting
  model the same way; it defaults to `gpt-4.1`. **Never commit a key** - both
  locations are gitignored, and `check_repo.py` fails the build if one slips in.

```bash
pip install -r requirements.txt
python -m playwright install chromium   # only needed for PDF output
```

## Running

```bash
python run.py                    # one client, full pipeline
python run.py --client CARLOS    # another client
python run.py --all              # every client in data/reference/clients.csv
python run.py --all --no-llm     # triage only: metrics and rules, no model calls
python run.py --fetch            # re-download market data first (needs network)
```

Or the advisor interface:

```bash
streamlit run app.py
```

### Twenty-four months of history

The monthly trajectory of each holding comes from its own collector, because the
cost is different in kind: equity closes and the Bacen series are two API calls,
while CVM publishes fund quotas only inside the whole month's informe, so one
bulk download per month.

```bash
python src/fetch_history.py             # everything missing (~20 CVM months)
python src/fetch_history.py --no-cvm    # equities and macro only, no bulk downloads
```

Anything already on disk wins over the network, zipped or not: drop
`inf_diario_fi_202401.zip` into `CSVs exportados CVM/` (or `data/raw/cvm/`) and
that month is read straight out of the archive, no unzipping. That is the manual
fallback when a download fails.

It is resumable - months already in the extract are skipped, so an interrupted
run costs nothing - and it keeps no archive: each month is read for the handful
of rows the holdings need and then discarded. What survives is
`data/raw/history_extract/monthly_series.csv`, a few KB, versioned, and enough
for `src/history.py` to rebuild every trajectory on a host with no network.

Two holdings cannot have a full series and the report says so on the page rather
than papering over it: the Brave FIDC only appears in CVM's FIDC informe from its
November 2024 re-registration, and the C6 CDB matured in September 2024 and is
carried at the statement's value. Quantities are held constant across the window
- no contributions or withdrawals - which is an assumption, printed as one.


## Layout

```
src/            pipeline stages, each runnable on its own
templates/      the letter template (HTML + print CSS)
data/reference/ human-curated inputs: instruments, positions, policies, answer keys
data/raw/       downloads, immutable, with a provenance manifest
output/<id>/    everything a run produces, per client
```

`data/reference/` is where the hard-won knowledge lives: which CNPJ belongs to
which fund, which ticker was renamed, which price the vendor back-adjusted, and
the suitability limits with their source. It is curated by a human on purpose.

## Offline mode

The CVM bulk files are ~230 MB and are not versioned. Any run that has them
writes the rows it used to `data/raw/cvm_extract/fund_quotas.csv` (~20 KB); a
run without them reads that extract instead and says so in the data-quality
log. The numbers are identical — the answer-key validation passes either way.

PDF rendering needs a browser. `render_pdf.py` tries three in order: the one
Playwright downloads, one installed by the system, then none. On a host that
forbids the download, `packages.txt` asks for a system Chromium — that file is
consumed line by line by the system package installer and **must contain
nothing but package names**, no comments. If no browser is available the HTML
letter is still produced and the run reports that the layout gate did not run;
the template carries A4 print rules, so the browser's own "save as PDF"
produces the same two pages.

## What this deliberately does not do

- It does not name a specific product to buy. Sells identify instruments,
  because the rules know exactly which positions breach; purchases name a
  product family and a credit criterion, because choosing an issuer needs a
  research source this system does not have.
- It does not optimise toward a target allocation. No such target is declared
  anywhere, so inventing one would be exactly the failure this pipeline exists
  to prevent.
- It does not report a validation that did not run. Only one client has a
  hand-checked answer key; for the others the script says so out loud.
