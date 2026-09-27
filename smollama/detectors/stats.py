"""Robust statistics for the detector layer.

Median and MAD rather than mean and standard deviation, for one reason: std is
inflated by the very outlier being detected. A baseline containing one 9999 gets
a standard deviation large enough to hide a genuine step change, so a z-score
built on it under-reports exactly when it matters. MAD barely moves.

All functions return None rather than raising or returning inf on degenerate
input — a single bad source must not take down a detection pass.
"""

# Scale factor making MAD a consistent estimator of sigma for normal data, so a
# robust z is roughly comparable to a conventional one (0.6745 = Phi^-1(0.75)).
_MAD_TO_SIGMA = 0.6745


def median(values) -> float | None:
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    nums.sort()
    mid = len(nums) // 2
    if len(nums) % 2:
        return nums[mid]
    return (nums[mid - 1] + nums[mid]) / 2.0


def mad(values) -> float | None:
    """Median absolute deviation from the median."""
    med = median(values)
    if med is None:
        return None
    return median([abs(float(v) - med) for v in values if isinstance(v, (int, float))])


def robust_z(value: float, baseline) -> float | None:
    """How many robust sigmas `value` sits from the baseline's median.

    None when the baseline is empty or has zero spread. Zero spread is not an
    error — it means the source is constant, which the flatline detector owns.
    Returning inf here would make every subsequent reading look infinitely
    anomalous.
    """
    med = median(baseline)
    dispersion = mad(baseline)
    if med is None or not dispersion:
        return None
    return (float(value) - med) / (dispersion / _MAD_TO_SIGMA)


def slope_per_hour(samples) -> float | None:
    """Least-squares slope in value-units per hour.

    Ordinary least squares is acceptable here because the trend detector also
    requires a monotonic majority before it fires, so a lone outlier cannot
    manufacture a trend on its own.
    """
    pts = [
        (s.ts.timestamp(), float(s.value))
        for s in samples
        if isinstance(s.value, (int, float))
    ]
    if len(pts) < 3:
        return None
    n = len(pts)
    t0 = pts[0][0]
    xs = [(t - t0) / 3600.0 for t, _ in pts]
    ys = [v for _, v in pts]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom


def monotonic_fraction(values) -> float:
    """Fraction of consecutive steps moving in the majority direction.

    Distinguishes a genuine drift from noise with a nonzero fitted slope.
    """
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    steps = [b - a for a, b in zip(nums, nums[1:]) if b != a]
    if not steps:
        return 0.0
    ups = sum(1 for s in steps if s > 0)
    return max(ups, len(steps) - ups) / len(steps)
