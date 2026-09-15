# Brave and closing balance reconciliation

The locally retained CVM monthly filings were checked directly for CNPJ
35.726.300/0001-37, March and April 2025. The senior series is the only series
with nonzero quota count; the subordinate series has zero quotas. The selected
source fields are recorded in `data/reference/brave_cvm_reconciliation.csv`.

The calculation keeps its existing full-precision NAV / quota-count method:

- March: 1114582178.73 / 634508766.93 = 1.756606428186614.
- April: 1104714760.69 / 621485524.21 = 1.7775390055854572.
- Monthly return: 1.1916486848%, consistent with the filing's rounded 1.19%.
- Carried opening position: R$ 72,567.43; closing position: R$ 73,432.18.

The former quota key (1.756614 and 1.777464) does not reproduce these source
fields. The former position key R$ 73,430.98 reproduces neither that quota pair
nor the full-precision pair. Both reference inconsistencies are corrected,
without editing positions.csv or the original case documents.

The closing total remains R$ 410,705.27, invested R$ 336,032.65, and funds
R$ 229,524.20. The former total key R$ 410,704.08 was inconsistent with the
source-based position calculation. Rounded position amounts are summed, so
rounding a difference between old and new Brave entries is not sufficient to
reconstruct the total. The reference now checks closing values as well as
opening values, returns and contributions. Quota comparisons use 1e-8 relative
tolerance; currency comparisons allow R$ 0.05 for rounded reference amounts.

Equity exposure and its profile/horizon ceiling now use closing total wealth,
including cash: 99172.85 / 410705.27 = 24.1470%, below the 25% ceiling.
The macro target remains 8.71%; being above that target is distinct from
breaching the profile ceiling. Product concentration remains measured within
each asset basket.
