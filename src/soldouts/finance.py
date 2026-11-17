"""Scenario arithmetic, without model calls or dependencies."""
from dataclasses import dataclass, asdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .data import UK


@dataclass(frozen=True)
class Scenario:
    added_capacity: int
    incremental_fill_rate: Decimal
    rep_minutes: int
    hourly_rep_cost_gbp: Decimal
    excess_refund_gp_loss_gbp: Decimal


DEFAULT_SCENARIOS = {
    "low": Scenario(5, Decimal("0.25"), 12, Decimal("25"), Decimal("5")),
    "base": Scenario(10, Decimal("0.50"), 12, Decimal("25"), Decimal("2")),
    "high": Scenario(15, Decimal("0.75"), 12, Decimal("25"), Decimal("0")),
}


def merchant_history(rows, as_of, *, months=6):
    """Use a fixed lookback of fully elapsed calendar months for one merchant."""
    local_day = as_of.astimezone(UK).date()
    cutoff = local_day.year * 12 + local_day.month
    eligible = {}
    for row in rows:
        month = row.get("month") or ""
        try:
            period = date.fromisoformat(month + "-01")
        except ValueError:
            continue
        if cutoff - months <= period.year * 12 + period.month < cutoff:
            eligible.setdefault(month, []).append(row)
    gp, vouchers, used = Decimal(0), 0, []
    for month, candidates in sorted(eligible.items()):
        if len(candidates) != 1:
            continue  # Ambiguous aggregates must not be counted twice.
        row = candidates[0]
        try:
            net = Decimal(row["gp_gbp"])
            count = int(row["vouchers_sold"])
        except (KeyError, ValueError, InvalidOperation):
            continue
        if net.is_finite() and count >= 0:
            gp += net
            vouchers += count
            used.append(month)
    return {"months": used, "net_gp_gbp": gp, "vouchers_sold": vouchers,
            "net_gp_per_voucher_gbp": gp / vouchers if vouchers else None}


def forecast(net_gp_per_voucher, scenarios=None):
    if net_gp_per_voucher is None:
        return {"status": "insufficient_data", "reason": "No usable prior voucher history."}
    if not net_gp_per_voucher.is_finite():
        raise ValueError("Net GP per voucher must be finite")
    results = {}
    for name, settings in (DEFAULT_SCENARIOS if scenarios is None else scenarios).items():
        fill = settings.incremental_fill_rate
        rep_cost = Decimal(settings.rep_minutes) * settings.hourly_rep_cost_gbp / 60
        risk = settings.excess_refund_gp_loss_gbp
        if (not all(v.is_finite() and v >= 0 for v in (fill, rep_cost, risk))
                or fill > 1 or min(settings.added_capacity, settings.rep_minutes,
                                   settings.hourly_rep_cost_gbp) < 0):
            raise ValueError(f"Invalid assumptions for {name} scenario")
        extra_gp = settings.added_capacity * fill * net_gp_per_voucher
        results[name] = {"assumptions": asdict(settings),
                         "incremental_net_gp_gbp": pennies(extra_gp),
                         "rep_cost_gbp": pennies(rep_cost),
                         "expected_net_value_gbp": pennies(extra_gp - rep_cost - risk)}
    return {"status": "scenario_estimate", "scenarios": results}


def pennies(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
