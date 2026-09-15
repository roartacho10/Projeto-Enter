# Macro-driven allocation

The equity ceiling and current equity exposure use closing total wealth,
including cash. Being above the macro target is distinct from exceeding the
profile/horizon ceiling. Product caps still use each asset basket as their base.
See [the Brave reconciliation](brave-reconciliation.md) for the source audit and
closing-value validation.

This case model proposes a class allocation, not a statistically optimal
portfolio or an individual-security return forecast. All operations are
suggestions for discussion with the client. No orders are executed.

## Immutable inputs and configuration

`data/reference/positions.csv` remains the hand-curated authoritative portfolio.
It is only read. The original TXT documents are not modified.

`macro_projections.csv` contains the deterministic extraction of the research
table. GDP, IPCA and Selic projections for each required year must appear
exactly once; missing, duplicate or non-finite projections stop the calculation.
There is no fallback to a guessed number or a later market observation.

`profile_limits.csv` retains the original risk-class/horizon equity ceilings.
Legacy cash and single-position columns have been removed: those parameters
now belong exclusively to `rebalance_policy.csv`.

`allocation_policy.csv` declares the user-approved calibration: annual dividend
yield 5%, cumulative spread sensitivity 10 percentage points, forecast endpoint
December 2026. These are model assumptions, not estimates learned from data.

## Arithmetic

Rates below are fractional rates; published fields use percentage units.

- Annual real RV return: `real GDP growth + assumed dividend yield`.
- Annual real RF return: `(1 + Selic) / (1 + IPCA) - 1`.
- Cumulative real return: multiply `(1 + annual real return)^(months / 12)`.
- Spread in percentage points: `100 * (cumulative RV - cumulative RF)`.
- Macro factor: `clip(0.5 + spread_pp / (2 * sensitivity_pp), 0, 1)`.
- Target RV percentage: `profile/horizon ceiling * macro factor`.
- Target RF percentage: `100 - target RV`; target cash: zero.

For the April 2025 closing portfolio, the forecast includes May-December 2025
(eight months) and all twelve months of 2026. The year-end Selic projection is
an annual-rate proxy, not a reconstructed average interest-rate path. The model
omits taxes, transaction costs and forecast uncertainty. It estimates classes,
not the future return of individual holdings.

The existing risk-category map assigns equities, equity funds and long-biased
funds to RV. Remaining investments are grouped into RF, including credit and
multimarket funds: this is a simplified allocation bucket, not an assertion
that all these products have the same risk or earn Selic.

## Suggested transition

1. Compute class targets from closing wealth, rounded to cents. RF receives
   the residual cent so that targets sum exactly to wealth.
2. Suggest full redemption of matured products and sale of existing RV
   positions. Propose the target RV amount in an unspecified Ibovespa index
   fund; do not select a ticker or manager.
3. Retain RF products up to 25% of the target RF basket each. If retained RF
   still exceeds the target, trim the largest remaining positions first.
4. Allocate RF shortfall among enough new, unspecified RF products to respect
   the same cap. The cap is per product, not per issuer; no issuer aggregation
   or actual credit-rating lookup is performed.
5. Reconstruct all post-trade positions using integer cents. Confirm wealth
   conservation, zero cash, exact class targets, no matured holdings and RF caps.

The index fund may occupy the entire RV basket. It diversifies company exposure
but retains market risk. If the client prefers picking stocks, the alternative
policy caps each individual stock at 25% of the RV basket. The default proposal
does not select stocks or produce an alternative stock portfolio.

Exact targets and zero cash supersede the former minimum-ticket heuristic:
small adjustments are possible. No attempt is made to optimise taxes, liquidity,
tradable lots, fees, minimum subscriptions or the number of operations.

`resolved` covers numerical allocation checks. `pending_reviews` separately
reports restricted-category findings on retained holdings; a numerically valid
allocation must not be described as universally suitable. Actual new products,
ratings and execution conditions still require assessment.

## Execution and presentation

`recommend.py` computes and saves `allocation_target.json` before producing
findings. `rebalance.py` consumes the matching target and saves the simulation.
`figures.py` authorises both historical and projected figures separately.

The Streamlit **Atualizar dados** action recalculates these stages from saved
inputs; it does not download market data or change `positions.csv`. The panel
shows the class targets, cumulative forecasts, annual inputs, calibration,
suggested trades and pending reviews. **Gerar relatório** uses those results.

The letter prompt and deterministic PDF summary distinguish estimates from
realised performance. The report groups existing RV sales into one table row
to keep the two-page layout; the panel and JSON retain each suggested sale.
Old-model reports and reports with stale figures are hidden until regenerated.
Reproved letters stop the generation pipeline; a render receipt distinguishes
current HTML/PDF from an older file left on disk. Running with `--no-llm` does
not re-render a previously written letter with changed data.

## Validation

Tests check independent compound-return arithmetic, monotonic macro response,
profile ceilings, forecast boundaries, missing/duplicate data rejection,
integer-cent cash flows, index migration, per-product RF caps and preservation
of the immutable portfolio. The deterministic integration suite runs in an
isolated copy without CVM bulk files, market downloads or model calls.
