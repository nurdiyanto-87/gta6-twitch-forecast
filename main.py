"""Forecast GTA VI's launch-day Twitch viewers, hour by hour (hours 2-24).

Model (pure stdlib, deterministic, no network):
  Every past big launch in the case's `comparables` has a release-day hourly
  curve.  For each hour h we model the log-ratio to that launch's own hour-1
  print, ln(v_h / v_1), as a straight line in log10(v_1): small launches
  explode in the evening (their hour 1 is a quiet pre-unlock hour), huge
  launches stay roughly flat.  The line is fit by least squares over the
  comparables whose unlock rolled at local midnight -- the same unlock
  pattern as GTA VI and the practice launches -- and evaluated at this
  case's own hour-1 print.  The spread around the line is the residual
  standard deviation per hour, widened by the regression's leverage where
  the case's hour-1 print sits far from the comparables' scale range, so
  the five quantiles are the fitted median widened by the normal z-scores.  Ratios are clamped to [0.3, 15] and the
  evaluation point is clamped to the fitted range, so an hour-1 print
  larger than any comparable cannot produce an absurd forecast.

  If too few rolling-midnight comparables exist, fall back to all
  comparables; if there are still too few, fall back to a flat persistence
  forecast with a widening spread.
"""

import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]

# Spread calibration: residual sd is measured in-sample, so it is shrunk a
# little; 0.8 was the best of {0.7, 0.8, 0.9, 1.0} on the practice cases.
SIGMA_MULT = 0.8
SIGMA_FLOOR = 0.25
RATIO_MIN = 0.3
RATIO_MAX = 15.0
MIN_CURVES = 5
MIN_HOURS = 18


def parse_ts(text):
    """ISO-8601 UTC timestamp -> datetime (accepts a trailing Z)."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def round2(x):
    """Round to 2 decimals, half up, returning an int for whole numbers."""
    scaled = x * 100.0
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100.0
    return int(value) if value == int(value) else value


def release_day_curve(comp):
    """Hourly viewers on the comparable's release day: {hour 1..24: viewers}."""
    try:
        day_start = parse_ts(comp["day_start"])
    except (KeyError, TypeError, ValueError):
        return {}
    curve = {}
    for point in comp.get("hourly") or []:
        try:
            at = parse_ts(point["from"])
            viewers = point["viewers"]
        except (KeyError, TypeError, ValueError):
            continue
        if viewers is None:
            continue
        delta = (at - day_start).total_seconds() / 3600.0
        if 0.0 <= delta < 24.0:
            curve[int(delta) + 1] = float(viewers)
    return curve


def is_rolling_midnight(comp):
    """True when the comparable unlocked at local midnight, rolling by zone."""
    unlock = (comp.get("unlock") or "").lower()
    return "rolling" in unlock or "midnight local" in unlock


def fit_line(xs, ys):
    """Least-squares y = a + b*x.

    Returns (a, b, sd, mx, sxx, n): the intercept, slope, residual standard
    deviation, mean of x, sum of squared x-deviations, and point count.  The
    last three let the caller widen the spread where the prediction is an
    extrapolation (leverage), which matters when the case's hour-1 print is
    larger than any comparable's.
    """
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx > 0 else 0.0
    intercept = my - slope * mx
    if n > 2:
        resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
        sd = statistics.pstdev(resid)
    else:
        sd = SIGMA_FLOOR
    return intercept, slope, sd, mx, sxx, n


def median_and_sigma(curves, hour, lx):
    """Fitted (log median ratio, sigma) for one hour at log10(hour-1) = lx."""
    points = []
    for curve in curves:
        if hour in curve and 1 in curve and curve[1] > 0 and curve[hour] > 0:
            points.append((math.log10(curve[1]), math.log(curve[hour] / curve[1])))
    if len(points) < MIN_CURVES:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    intercept, slope, sd, mx, sxx, n = fit_line(xs, ys)
    # Do not extrapolate far past the fitted scale range.
    lo, hi = min(xs), max(xs)
    lx_c = max(lo - 0.3, min(hi + 0.3, lx))
    median_log = intercept + slope * lx_c
    median_log = max(math.log(RATIO_MIN), min(math.log(RATIO_MAX), median_log))
    # Prediction spread: residual scatter plus the regression's own
    # uncertainty at this x (1 + 1/n + leverage), so a case far outside the
    # comparables' scale range gets honestly wider quantiles.
    leverage = (lx_c - mx) ** 2 / sxx if sxx > 0 else 0.0
    sigma = max(SIGMA_FLOOR, math.sqrt(sd * sd * (1.0 + 1.0 / n + leverage)) * SIGMA_MULT)
    return median_log, sigma


def forecast_case(case, name):
    if case.get("levels") != LEVELS:
        raise ValueError(f"{name} asks for levels {case.get('levels')}, not {LEVELS}")
    first = next((k for k in case["known"] if k["hour"] == 1), None)
    if first is None:
        raise ValueError(f"{name} has no hour-1 print")
    p1 = max(1.0, float(first["viewers"]))
    lx = math.log10(p1)

    curves = []
    for comp in sorted(case.get("comparables") or [], key=lambda c: c.get("name", "")):
        curve = release_day_curve(comp)
        if 1 in curve and curve[1] >= 500 and len(curve) >= MIN_HOURS:
            curves.append((is_rolling_midnight(comp), curve))
    rolling = [c for flag, c in curves if flag]
    chosen = rolling if len(rolling) >= MIN_CURVES else [c for _, c in curves]

    hours = []
    for row in case["forecast"]:
        h = row["hour"]
        fitted = median_and_sigma(chosen, h, lx) if len(chosen) >= MIN_CURVES else None
        if fitted is None:
            # Persistence fallback: flat median, spread widening like a
            # random walk in log space (the house baseline's shape).
            median_log, sigma = 0.0, 0.15 * math.sqrt(h - 1)
        else:
            median_log, sigma = fitted
        quantiles = []
        for z in Z:
            q = p1 * math.exp(median_log + z * sigma)
            q = max(1.0, min(1e9, q))
            quantiles.append(round2(q))
        # Keep the row non-decreasing after rounding.
        for i in range(1, len(quantiles)):
            if quantiles[i] < quantiles[i - 1]:
                quantiles[i] = quantiles[i - 1]
        hours.append({"hour": h, "q": quantiles})
    return {"hours": hours}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
