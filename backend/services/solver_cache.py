"""Result caches for the combo recommender and the optimizer, cleared whenever a
sync publishes a new item graph."""

import threading

from solver_cache_epoch import SolverCacheEpochTracker

# ---------------------------------------------------
# Combo recommender results
# ---------------------------------------------------

COMBO_FULL_CACHE: dict = {}
COMBO_FULL_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------
# Optimizer results
# ---------------------------------------------------

OPTIMIZE_CACHE: dict = {}
OPTIMIZE_CACHE_LOCK = threading.Lock()
_SOLVER_CACHE_EPOCH_LOCK = threading.Lock()
_SOLVER_CACHE_EPOCH_TRACKER = SolverCacheEpochTracker()


def clear_solver_caches():
    """Invalidate every result derived from the synchronized item graph."""
    with COMBO_FULL_CACHE_LOCK:
        COMBO_FULL_CACHE.clear()
    with OPTIMIZE_CACHE_LOCK:
        OPTIMIZE_CACHE.clear()


def solver_cache_generation() -> str:
    """Clear this worker's caches after another process publishes a sync."""
    with _SOLVER_CACHE_EPOCH_LOCK:
        generation, changed = _SOLVER_CACHE_EPOCH_TRACKER.refresh()
        if changed:
            clear_solver_caches()
        return generation
