# Issue 60: correctness and solve-time investigation

The active optimizer retains the earlier setup and sparse-matrix speed improvements.
No issue 60 correction is enabled: every tested sound replacement materially slowed
the affected M4 Explore curves, and the dense TrueErgo case reached the existing
30-second deadline. Keep this investigation separate from the performance changes.

Issue: <https://github.com/SouthHorizons76/EFTForge/issues/60>.

## Reproduction

The existing tangent cuts can exclude valid attachment selections. Validating the
returned build's stats does not establish optimality when those selections have
already been excluded. An exhaustive three-choice fixture reproduced both a worse
result marked optimal and false infeasibility. The initial regression run had
32 failures and two passes.

With M4 `5447a9cd4bdc2dbd208b4567`, M855 `54527a984bdc2d4e668b4567`, full magazines,
at least 60-round capacity and PvP prices, the seed-zero baseline returned recoil
48 at TED floor -5.22 and recoil 49 with overswing prevention. The sound one-hot
prototype returned recoil 47 and 48 respectively across hash seeds 0 through 3.
The database and runtime differ from the issue author's original reproduction.

## Explore measurements

Use eight Explore workers, the same read-only database, persistent subprocesses,
alternating A/B order and the application's unchanged 30-second deadlines. The
baseline includes the earlier optimizer speed improvements in this workspace.

| Formulation | M4 TrueErgo and overswing prevention, 10 steps | 81 steps |
| --- | --- | --- |
| One-hot secants | 2.482 to 6.122 s | 7.133 to 30.036 s, partial |
| Log-coded secants | 2.313 to 30.013 s, partial | Not repeated |
| Global chord and retry shortcut | 2.274 to 6.460 s | 7.212 to 30.029 s, partial |
| Hybrid and rounded-floor bound | 2.380 to 7.561 s | 7.501 to 30.015 s, partial |

The one-hot 10-step result is the median of three measured pairs after one warmup.
Its density result uses one pair. Global and hybrid results use one measured pair
after one warmup. The logarithmic result uses one pair. These are local measurements,
not timing guarantees.

The final hybrid retained seven frontier points at 81 steps; the baseline completed
with 14. Ordinary M4 curves retained exact event output; the three-pair one-hot
comparison measured 0.986 to 0.958 seconds. AK-101 overswing curves changed little.
Changing candidate ordering alone would not repair the invalid feasible region.
The sampling method, point density, game formulas and solver options were preserved.

Across the recorded runs, 2,881 returned-build audits passed shared stats and active
constraint checks. This establishes build validity, not global optimality. The
small exhaustive regression oracle and outer-bound proof address the latter for
the existing linear objectives, within the solver's existing numerical tolerances.

Runtime: Python 3.12.10, NumPy 2.5.3, SciPy 1.18.1, SQLAlchemy 2.0.52, Windows 11.
The unchanged database SHA-256 is
`b26887567322e6ba260b7828cd23fe8e6cdb809b73f2e7fd0b9dddb643523ca9`.
See [the aggregate evidence](results/issue60_summary.json) for source hashes,
repetition counts, status, frontier yield and individual measurements. Temporary
directory names in that JSON identify the original measurement stages.
[The compressed raw evidence](results/issue60_raw_evidence.zip) retains the paired
measurement streams, full curve events, per-build audits, harness and baseline
Python sources. Its SHA-256 is recorded in the aggregate evidence.

## Reviewable correction

[issue60_secant.patch](proposals/issue60_secant.patch) contains the final tested
hybrid prototype and its regressions. It applies on top of the earlier speed
changes currently in the workspace. The patch is archived for review and is not
part of the active Python implementation.
The patch passes `git apply --check`. After restoring the active optimizer,
23 setup, sparse-matrix, cache and termination regressions passed; the restored
optimizer files match the prior speed-work snapshot byte for byte.

For modeled margin m, optimistic effective ergonomics S and weight W, the exact
uncapped boundary above 3 kg is f(W) = 100 + m - 300/W. This function is concave.
Its secants lie below the boundary over their intervals. Selecting the minimum
segment line through a binary disjunction therefore retains every feasible build.
A global endpoint chord strengthens the continuous relaxation. Positive floors
also require W <= 300/m because effective ergonomics caps at 100.

The patch uses conservative factory and ammo-rounding allowances, the existing
exact stats oracle, displayed-TED floor checks and strict overswing checks.
Rejected selections receive sound adaptive disjunctions or exact no-good rows.
Local price cleanup fills auxiliary assignments and repeats the exact aiming
check. The final hybrid avoids the initial grid for positive margins and rounds
the model floor outward to the next eligible displayed hundredth.

The full backend suite passed with the prototype: 424 passed and three skipped
in 77.65 seconds. Black and flake8 passed. Preservation regressions enumerate
768 attachment selections and confirm that valid selections survive every learned
cut. Tests also cover equipment scale zero, ammo and UBGL weights, factory overrides,
the 100-point cap, half-cent TED boundaries, incomplete solve status, auxiliary
integrality and local price cleanup. No frontend checks were performed.

The existing TrueErgo objective anchor sweep remains an approximation. This patch
does not establish a new global nonlinear TrueErgo objective optimum. Further
speed work would need independent measurements. Sharing request-local secant
geometry between tiers is mathematically possible with matching affine inputs,
adequate weight-domain coverage and freshly computed row bounds, but its speed
benefit has not been demonstrated.

## Reproduce a curve comparison

Apply the archived patch to a separate candidate checkout that already contains
the earlier speed changes. Run the existing curve benchmark against the active
baseline and that candidate, with the same frozen item database:

```powershell
$env:EFTFORGE_EXPLORE_WORKERS = '8'
backend\venv\Scripts\python.exe backend\benchmarks\explore_ab.py `
  --baseline C:\path\to\baseline\backend `
  --candidate C:\path\to\candidate\backend `
  --database backend\tarkov.db `
  --cases-json backend\benchmarks\issue60_cases.json `
  --warmups 1 --runs 3 --output C:\path\to\new-results
```

Keep the default budget. Run `tests/test_optimizer_ted_outer.py` in the candidate
for the database-independent exhaustive regressions.
