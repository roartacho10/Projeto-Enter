# Monthly investment report — pipeline

A proof of concept that produces a client-ready monthly investment letter from
public data, for a middle-market brokerage client.

## Try the application

[Open the Streamlit application](https://projeto-enter-wpiqdnwzhhxnxibr5nwmga.streamlit.app/).
The demonstration uses Albert's portfolio for April 2025.

1. Click **Acessar** to open the advisor interface.
2. Review Albert's performance, profile, current allocation and suggested adjustments.
3. In the client letter section, click **Gerar carta de Albert**.
4. Once generation and verification finish, download the PDF or HTML.
   Email delivery is optional and requires the [Gmail configuration](docs/email.md).

The other clients are demonstration roster entries without positions; Albert is
the complete case. The hosted app uses server-side credentials when configured.

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

The recommendation engine now uses the approved macro allocation model. See
[Macro-driven allocation](docs/macro-allocation.md) for formulas, calibration,
the suggested index migration, per-basket product limits and limitations.

1. **Ingests** prices from three public sources: CVM (fund quotas, daily and
   FIDC monthly), B3 via Yahoo (equities and the Ibovespa) and the Brazilian
   Central Bank (CDI, Selic, IPCA), and reads the house macro report's
   projections table deterministically - no model involved. The table is
   printed twice in that document and both copies must agree; figures the
   report's own narrative restates are checked against it, and the **table is
   the authority**. Where the two disagree the divergence is recorded and the
   table's value is what the letter is allowed to quote.
2. **Computes** the month's return, per-position attribution and look-through
   equity exposure — all in code, validated against a hand-checked answer key.
   Performance is stated against **two declared references, never blended**: the
   CDI, which answers whether the risk paid, and the IPCA, which answers whether
   the money kept its purchasing power. An earlier version quoted a weighted
   "75% CDI + 25% Ibovespa" that no input file declared and that averaged a cash
   rate with an equity index into a figure describing neither. Real return is a
   deflation, `(1+r)/(1+i)-1`, not a subtraction. The Ibovespa is reported as
   market context only.
3. **Applies** suitability rules read from CSV, not hard-coded, each carrying
   the clause of the client's risk-profile document it comes from.
4. **Allocates** between RV and RF using cumulative real class-return estimates
   through December 2026, within the profile/horizon equity ceiling. It proposes
   zero cash, migration of current RV to an unspecified Ibovespa index fund and
   a 25% per-product limit within RF (also per individual stock within RV if the
   client prefers stock picking; the index fund is exempt). It simulates the
   monetary trades and separately reports pending category reviews.
5. **Writes** the letter with a language model that receives a list of
   authorised figures and is told to quote them verbatim.
6. **Verifies** in two layers. The figure gate checks exact numeric equality
   against the authorised sheet, accepting trailing-zero formatting differences
   without rounding or tolerance. Then the **claim gate**
   (`src/claims.py`) confronts what the sentences ASSERT with the same data: an
   instrument said to have risen when it fell, a trade recommended for a position
   the plan does not touch, "beat the CDI" when the excess is negative,
   compliance claimed while breaches are open, past-tense execution language in
   a letter that only recommends - and, the deepest of the six, a figure attached
   to the **wrong position**. "LREN3, com retorno de 18,94%" quotes an authorised
   number and misattributes it; only the claim gate sees that. Each figure is
   attributed to the nearest position named before it, so an enumeration of three
   holdings reads correctly while a swap inside it does not. The gate checks a
   declared catalogue of claim shapes, not arbitrary prose, and **reports its own
   coverage** rather than implying completeness. Failures of either layer are fed
   back and the model tries again.
7. **Renders** a fixed two-page PDF from a deterministic template, refusing to
   publish if the content does not fit the declared layout budget.

## Requirements

- Python 3.13 (tested on 3.13.14)
- An OpenAI API key. The app looks for it in `.streamlit/secrets.toml` (see
  `.streamlit/secrets.toml.example`), then in the `OPENAI_API_KEY` environment
  variable, and only asks in the expandable letter configuration if neither is set. On Streamlit
  Community Cloud, put it in Settings > Secrets. `MODEL_LETTER` sets the drafting
  model the same way; it defaults to `gpt-4.1`. **Never commit a key**.
  `.env` and `.streamlit/secrets.toml` are gitignored; `python check_repo.py`
  checks the deliverable files for common credential patterns.

```bash
pip install -r requirements.txt
python -m playwright install chromium   # only needed for PDF output
```

## Running

For the commands below, set `OPENAI_API_KEY` in the terminal environment before
generating a letter. The command-line pipeline does not read Streamlit Secrets
or automatically load `.env`. The `--no-llm` mode requires no API key.
The Streamlit interface reads its own Secrets configuration as described above.

```bash
python run.py                    # one client, full pipeline
python run.py --client ALBERT    # explicitly select the complete case
python run.py --all              # all registered clients with positions
python run.py --all --no-llm     # triage only: metrics and rules, no model calls
python run.py --fetch            # re-download market data first (needs network)
```

Or the advisor interface:

```bash
streamlit run app.py
```

## Submission files

Keep the source, tests, templates, assets, documentation and versioned data.
`data/reference/positions.csv` is the manually prepared authoritative portfolio;
the case documents in `data/raw/inputs/` and the policy tables are also required.
The small market-data extracts allow deterministic calculations without bulk CVM downloads.

Generated outputs, local drafts, caches and credentials are excluded by `.gitignore`.
For a ZIP submission, use GitHub's repository download instead of compressing the
entire local working folder. A freshly generated client letter can be attached
separately as an example of the output.

### Tests

```bash
python -m pytest -q          # deterministic tests; no market downloads or model calls
```

The suite copies out **only the files git publishes** and runs the
deterministic stages there, so a green run also proves the repository is
self-sufficient: the portability check and the test suite are the same act. If
`test_ran_in_offline_mode` ever fails, a fresh clone stopped being runnable.

What it holds to:

- period arithmetic across year boundaries and leap Februaries
- integrity of the curated reference data - no orphan position, no unused risk
  category, no undeclared `quantity_basis`, a complete and monotone limits matrix
- the metrics against the hand-checked answer key, and that per-position
  contributions add up to the total return
- the profile reading the three parameters *with the sentence that produced each*,
  and that no numeric limit claims the profile document as its source
- macro targets staying inside the profile ceiling, exact simulated cash flows,
  per-basket product caps, index migration and separate pending category reviews
- the verification gate: it passes a letter of authorised figures, rejects an
  invented one, does not mistake a ticker's digit for a claim, and catches
  forbidden language and a missing client name

The suite was mutation-tested: five deliberate defects were introduced and each
was caught. The fifth exposed a self-referential test - it read the minimum
ticket from the policy it was checking, so zeroing the policy made it pass
trivially. `test_min_ticket_is_meaningful` now guards that threshold's own
sanity.

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
tests/          pytest suite; runs in a clean checkout of the tracked files
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
- It uses a user-calibrated class target, not a statistically optimal portfolio.
  Expected real returns use GDP plus assumed dividend yield for RV and Selic
  deflated by IPCA for RF. The complete assumptions are documented above.
- It does not report a validation that did not run. Only one client has a
  hand-checked answer key; for the others the script says so out loud.
