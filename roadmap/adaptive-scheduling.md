# Adaptive Observation Scheduling

Dynamically adjust observation frequency based on sensor volatility.

- **Status**: Not started
- **Effort**: Small-Medium (30-40 hours)
- **Priority**: Medium
- **Dependencies**: Observation loop ✅, LocalStore ✅

## Overview

Currently, observations run on a fixed interval (default 15 minutes). This wastes resources
during stable periods and misses rapid changes during volatile periods. Adaptive scheduling
increases frequency when sensors change rapidly, decreases during stability.

## Goals

- Increase observation frequency when readings volatile
- Decrease frequency during stable periods
- Configurable min/max interval bounds
- Prevent oscillation (hysteresis)
- Save compute/memory during quiet periods

## Implementation

### Volatility Tracking
- Store last N readings for each sensor (e.g., last 10)
- Calculate coefficient of variation: `CV = stddev / mean`
- Or: Max delta over window: `max(|reading[i] - reading[i-1]|)`
- Thresholds: `high_volatility` (CV > 0.5), `low_volatility` (CV < 0.1)

### Interval Adjustment
- Start at default interval (15 min)
- If volatility high: Reduce interval by 50% (down to `min_interval`, e.g., 5 min)
- If volatility low: Increase interval by 50% (up to `max_interval`, e.g., 60 min)
- Hysteresis: Require 2-3 consecutive volatile/stable periods before adjusting

### Configuration
```yaml
memory:
  observation_enabled: true
  observation_interval_minutes: 15  # Default/initial interval
  adaptive_scheduling:
    enabled: true
    min_interval_minutes: 5        # Fastest (high volatility)
    max_interval_minutes: 60       # Slowest (stable)
    volatility_threshold_high: 0.5 # CV threshold for "high"
    volatility_threshold_low: 0.1  # CV threshold for "low"
    hysteresis_count: 3            # Require 3 periods before changing
```

## Files to Modify

- `smollama/memory/observation_loop.py` - Add volatility tracking
- `smollama/memory/local_store.py` - Add method to get recent reading deltas
- `smollama/config.py` - Add AdaptiveSchedulingConfig

## Algorithm Pseudocode

```python
def calculate_next_interval(current_interval, readings_history):
    volatility = calculate_volatility(readings_history)

    if volatility > HIGH_THRESHOLD:
        new_interval = max(current_interval * 0.5, MIN_INTERVAL)
    elif volatility < LOW_THRESHOLD:
        new_interval = min(current_interval * 1.5, MAX_INTERVAL)
    else:
        new_interval = current_interval  # No change

    return new_interval
```

## Testing

1. Configure adaptive scheduling in config.yaml
2. Start smollama with stable sensor (e.g., system CPU temp)
3. Verify interval increases to max (60 min)
4. Rapidly toggle GPIO sensor
5. Verify interval decreases to min (5 min)
6. Check logs for volatility calculations
