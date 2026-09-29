# Issue #27 — Recommendation impact and measured-error confidence

## Scope

Every generated `Recommendation` now includes deterministic impact estimates for the selected shipment and a qualitative confidence label derived only from measured recent forecast error.

## Impact

Impact is computed over the forecast horizon by comparing two deterministic inventory simulations:

1. the current world with no new shipment;
2. the same world with the recommended quantity arriving on the selected route's expected arrival tick.

The response exposes:

- `projected_stockout_ticks_avoided`
- `projected_unmet_demand_liters_avoided`
- `inventory_at_arrival_without_liters`
- `inventory_at_arrival_with_liters`

The simulation follows the existing inventory projection convention: forecast demand is consumed first, then incoming fuel is applied and capped by station capacity.

## Confidence

No probability is emitted.

For each recommendation, recent historical demand observations are scored one-step-ahead using a rolling calibration fitted only on observations strictly before the observation being scored. The implementation records:

- recent measured MAE in liters/tick;
- recent maximum absolute forecast-error band in liters/tick.

The qualitative confidence label is deterministic:

- `HIGH`: stress-margin magnitude is at least 2× the measured error band;
- `MEDIUM`: stress-margin magnitude is between 1× and 2× the measured error band;
- `LOW`: stress-margin magnitude is inside the measured error band;
- `INSUFFICIENT_DATA`: fewer than four recent observations.

These labels are descriptive robustness categories, not calibrated probabilities.

## Contract

The new fields are additive to `Recommendation`. They are documented in `docs/api-contracts.md` and surfaced in the operator dashboard.
