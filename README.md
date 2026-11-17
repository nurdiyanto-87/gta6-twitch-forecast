# GTA VI launch-day Twitch viewers — hour-by-hour forecast

Entry for make.fun competition `gta6-twitch-ccu-forecast-01`: forecast the
concurrent Twitch viewers in the Grand Theft Auto VI category for hours 2–24
of release day (2026-11-19 UTC), as five quantiles per hour, from everything
known up to the end of hour 1.

## Approach

The case ships 38–42 past big game launches with full hourly Twitch curves
(`comparables`). The key empirical fact in that data: **how a launch's
audience evolves over its release day depends on how big its first hour
is**. Small launches explode in the evening (their hour 1 is a quiet
pre-unlock hour, then the US unlock waves and prime time pile on — up to
~9x the first hour); huge launches stay roughly flat (hour 1 already holds
most of the world).

So for each hour `h` we fit, by least squares over the comparables whose
unlock rolled at local midnight (the same unlock pattern as GTA VI),
a straight line in log space:

```
ln(v_h / v_1) = a_h + b_h * log10(v_1)
```

evaluated at this case's own hour-1 print. The quantile spread is the
per-hour residual standard deviation, widened by the regression's leverage
where the case's hour-1 print sits far from the comparables' scale range —
an hour-1 print larger than any comparable gets honestly wider quantiles.
Median ratios are clamped to [0.3, 15] and the evaluation point to the
fitted scale range ±0.3 dex, so extrapolation cannot produce absurd
forecasts. If too few rolling-midnight comparables exist, the fit falls
back to all comparables, then to a flat persistence forecast.

Pure Python 3.13 standard library, deterministic (no randomness, no
network), ~0.1 s per case.

## Practice results

Scored locally against the four practice launches' real release-day
hourly counts (SullyGnome hourly averages, the same source the kit uses),
mean pinball loss on the log scale:

| case | launch | loss | house baseline |
|------|--------|------|----------------|
| p00 | Marvel's Wolverine | 0.186 | 0.744 |
| p01 | Red Dead Redemption 2 | 0.109 | 0.287 |
| p02 | God of War Ragnarök | 0.113 | 0.215 |
| p03 | Marvel's Spider-Man 2 | 0.220 | 0.303 |
| **mean** | | **0.157** | **0.387** |

## Run

```sh
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Reads every `<id>.in` in the input directory and writes `<id>.out`
(`{"hours": [{"hour": 2, "q": [q05, q25, q50, q75, q95]}, ...]}`) for each.
