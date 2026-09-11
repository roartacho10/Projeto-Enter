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
- An OpenAI API key, provided through the `OPENAI_API_KEY` environment variable
  or the app's sidebar. **Never commit a key.**

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

If Chromium is unavailable, the PDF step is skipped, the HTML letter is still
produced, and the run reports that the layout gate did not execute.

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
