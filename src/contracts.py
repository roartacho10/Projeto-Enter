"""
Typed contracts for the pipeline.

This module is the boundary between the deterministic half of the system
(ingestion, pricing, metrics) and the generative half (letter drafting).
Every number that reaches the language model must first exist as a field
on one of these models. Nothing is passed around as a loose dict.
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field

InstrumentType = Literal["stock", "index", "fund", "fixed_income", "cash"]
PricingSource = Literal[
    "yfinance", "cvm_inf_diario", "cvm_inf_mensal_fidc",
    "statement_carry", "manual_external", "par", "pending",
]
PricingFrequency = Literal["daily", "monthly", "none"]
QuantityBasis = Literal["statement_qty", "derive_from_value", "return_only", "value_only"]
Severity = Literal["info", "warning", "blocker"]


class Instrument(BaseModel):
    """Security master entry: what a thing IS, independent of who holds it."""
    instrument_id: str
    type: InstrumentType
    risk_category: str
    ticker_statement: Optional[str] = None
    ticker_current: Optional[str] = None
    cnpj: Optional[str] = None          # kept as a string: leading zeros matter
    official_name: str = ""
    statement_name: str = ""
    pricing_source: PricingSource
    pricing_frequency: PricingFrequency
    validated: bool = False
    note: str = ""


class CorporateAction(BaseModel):
    """A rename, split or reverse split. Tickers are not stable keys."""
    event_id: str
    instrument_id: str
    event_type: str
    event_date: date
    ratio: float = 1.0
    note: str = ""
    source: str = ""


class Position(BaseModel):
    """What a given client holds. Separate from Instrument on purpose."""
    client_id: str
    instrument_id: str
    quantity: Optional[float] = None
    quantity_basis: QuantityBasis
    avg_price: Optional[float] = None
    statement_value: float
    statement_value_date: date
    alloc_pct_statement: Optional[float] = None
    note: str = ""


class PriceRow(BaseModel):
    """One instrument, one date, one price - always with provenance."""
    instrument_id: str
    date: date
    price: float
    source: str
    retrieved_at: Optional[datetime] = None
    note: str = ""


class IndicatorRow(BaseModel):
    """Macro series point (CDI, Selic, IPCA...)."""
    series_id: str
    name: str
    date: date
    value: float
    source: str = "bcb_sgs"


class DataQualityIssue(BaseModel):
    """Anything the pipeline noticed and refused to silently absorb."""
    run_id: str
    severity: Severity
    instrument_id: Optional[str] = None
    code: str
    message: str


class PositionMetric(BaseModel):
    """Per-position result of the calculation engine."""
    instrument_id: str
    quantity: Optional[float] = None
    price_start: Optional[float] = None
    price_end: Optional[float] = None
    value_start: float
    value_end: float
    return_pct: float
    # Two weights, each with its base in the name. The opening weight is the
    # one attribution needs (contribution_pp is measured against the opening
    # total, which is why the contributions add up to the total return); the
    # closing weight is the one that describes the portfolio as it stands and
    # is the only one that reconciles with value_end. They were a single
    # ambiguous `weight_pct` and the report printed the opening weight beside
    # a closing value, under a heading naming the closing date.
    weight_start_pct: float
    weight_end_pct: float
    contribution_pp: float
    # No per-position comparison against the CDI or the IPCA lives here. It
    # was tried both ways and neither reads well across a mixed book: in
    # points it is fine for a fund and meaningless next to a stock, and as a
    # share of the reference a stock comes out at 2.837% of the CDI, which is
    # arithmetically true and tells the reader nothing. The comparison against
    # the references stays at the portfolio level, where it has a basis.
    pricing_note: str = ""


class MetricsPack(BaseModel):
    """
    The single source of truth for every figure in the client letter.
    If a number is not in here, the letter may not state it.
    """
    run_id: str
    client_id: str
    period_start: date
    period_end: date
    positions: list[PositionMetric] = Field(default_factory=list)
    total_value_start: float
    total_value_end: float
    total_return_pct: float
    invested_value_start: float
    invested_value_end: float
    invested_return_pct: float
    cash_value: float
    # Share of the CLOSING patrimony, the base the suitability rule and the
    # screen both measure against. It used to be a share of the opening
    # patrimony under a name that declared no base, so the letter quoted
    # 18,92% while the rule that fired quoted 18,18% for the same balance.
    cash_pct_of_total_end: float
    # Two declared references, never blended. A weighted mix of CDI and
    # Ibovespa was invented here and no document supported it; worse, it
    # averaged a cash rate with an equity index into one figure that describes
    # neither. CDI answers "was the risk worth taking"; IPCA answers "did the
    # money keep its purchasing power", which is this client's stated objective.
    cdi_return_pct: float
    ipca_return_pct: float
    excess_over_cdi_pp: float          # invested return minus CDI, same basis
    real_return_total_pct: float       # patrimonio deflated by IPCA, geometric
    ibov_return_pct: float             # market context, not a benchmark here
    coverage_pct: float
    issues: list[DataQualityIssue] = Field(default_factory=list)


RecommendationAction = Literal["allocate", "redeem", "reduce", "review", "hold"]


class Recommendation(BaseModel):
    """
    Produced by the deterministic rule engine, never by a language model.
    The model may only phrase what is already decided here.
    """
    rule_id: str
    action: RecommendationAction
    instrument_id: Optional[str] = None
    amount_brl: Optional[float] = None
    observed: str            # the measured fact that fired the rule
    observed_pct: Optional[float] = None   # same measure, structured, so it can
                                           # reach the figure sheet instead of the
                                           # model reaching for a lookalike number
    threshold: str           # the policy limit it breached
    rationale: str           # why this matters for THIS client's mandate
    policy_source: str       # where in the profile document this comes from
    severity: Severity


class RecommendationSet(BaseModel):
    run_id: str
    client_id: str
    profile: str
    recommendations: list[Recommendation] = Field(default_factory=list)
    equity_lookthrough_pct: float
    equity_reported_pct: float
    idle_capital_brl: float
    idle_capital_pct: float


TradeAction = Literal["sell", "redeem", "buy"]


class Trade(BaseModel):
    """One sized action. Sells name an instrument; buys name a category and the
    criterion it must satisfy, because naming a product needs a research source
    the system does not have."""
    action: TradeAction
    instrument_id: Optional[str] = None
    category: Optional[str] = None
    criteria: Optional[str] = None
    amount_brl: float
    min_issuers: Optional[int] = None
    min_products: Optional[int] = None
    asset_class: Optional[Literal["RV", "RF"]] = None
    reason: str
    rule_id: str


class RebalancePlan(BaseModel):
    """
    Suggested transition to a calibrated macro allocation. Resolved covers
    numerical targets and product caps; pending_reviews records other findings.
    """
    run_id: str
    client_id: str
    trades: list[Trade] = Field(default_factory=list)
    proceeds_brl: float
    deployable_brl: float
    cash_after_brl: float
    violations_before: list[str] = Field(default_factory=list)
    violations_after: list[str] = Field(default_factory=list)
    resolved: bool
    model_version: str = "legacy"
    allocation_fingerprint: str = ""
    post_positions: list[dict] = Field(default_factory=list)
    # Where the book stands against where the model puts it, by class and by
    # concentration. Every percentage here is a share of the SAME base - the
    # closing patrimony. The equity ceiling uses this same base, including cash.
    # Product concentration percentages are measured within their own baskets.
    class_mix: list[dict] = Field(default_factory=list)
    concentration: list[dict] = Field(default_factory=list)
    pending_reviews: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- history
HistoryBasis = Literal["quota_month_end", "close_month_end", "reported_monthly_return",
                       "compounded_daily_rate", "published_monthly_rate"]


class HistoryPoint(BaseModel):
    """One month of one series. `index` is base 100 at the series' first month."""
    month: str                          # YYYY-MM
    date: str                           # the published observation date
    price: Optional[float] = None       # absent for return-only sources
    return_pct: Optional[float] = None  # month over month
    index: float


class HistorySeries(BaseModel):
    instrument_id: str
    display_name: str
    kind: Literal["position", "benchmark"]
    basis: HistoryBasis
    source: str
    months_covered: int
    first_month: str
    last_month: str
    total_return_pct: float             # over the covered window, not the requested one
    complete: bool                      # covers every month the window asked for
    gap_note: str = ""
    points: list[HistoryPoint]


class HistoryPack(BaseModel):
    """Monthly trajectory of the positions. Constant quantities by assumption."""
    client_id: str
    window_start: str
    window_end: str
    months_requested: int
    assumption: str
    series: list[HistorySeries]
    excluded: list[dict]                # position -> why it has no series
    generated_at: datetime = Field(default_factory=datetime.utcnow)
