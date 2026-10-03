# Matching placement against current main

Retest date: 2026-10-03. Baseline: upstream
`a8356fec8636e36befcf52bdc8b8042a938c6a93`.
The PR is rebased onto this commit. Current main already accepts FD917 plus Tan
Baldr; the Glock example is regression coverage, not a new advantage over this
baseline. These measurements supersede the earlier cached/uncached comparison.

## Method

Use the same read-only game snapshot for both backends, SHA256
`2cdd4ebe5adc88c66d4f3b98aa5e8cb08598ed4ca020f4e834a1f08473b51dff`.
Python 3.12.14, NumPy 2.5.3, SciPy 1.18.1 and SQLAlchemy 2.0.52;
install current main's pinned backend dependencies. Set PYTHONHASHSEED=0 and
OPENBLAS/OMP/MKL thread environment variables to 1. Alternate isolated workers,
with one warmup and three sequential measurements per case. No competing test
suite runs during the measured requests.

Measure the complete Explore generator, including candidate preparation,
modeling, native solves, price cleanup and export. Exclude imports and independent
validation. Preserve the production 30-second request deadline. M4A1 uses full
market availability, an M855-loaded magazine of at least 60 rounds, and the same
450 candidates and 5,569 compatibility edges in both versions. Do not interpret
these small-sample local measurements as online concurrency or P95 results.

## Explore latency

| Case | Current main median | Matching median | Result |
|---|---:|---:|---|
| Ergonomics/recoil, 10 steps | 8.316 s | 4.037 s | 51.5% less time |
| Ergonomics/recoil, 40 steps | 28.765 s | 10.860 s | 62.2% less time |
| Ergonomics/recoil, 81 steps | 30 s budget, partial in 3/3 | 20.408 s, complete in 3/3 | Higher completion rate |
| TED/recoil, 10 steps | 13.947 s | 6.866 s | 50.8% less time |
| Ergonomics/price, 10 steps | 5.839 s | 2.185 s | 62.6% less time |
| Recoil/price, 10 steps | 9.115 s | 2.799 s | 69.3% less time |

The 81-step times compare a deadline-limited partial result with a completed
request, so no same-work speedup percentage is claimed. Completion means all
planned samples finished, not exhaustive enumeration of the Pareto frontier.

Keep 450 selection binaries in both models. Current main adds 4,110 continuous
placement columns, giving 4,561 total columns. Matching uses 451 total columns
and refines invalid selections through Hall cuts. Some paths need more native
calls: the TED case uses 40 versus 51. Smaller models, rather than uniformly
fewer calls, explain the remaining performance benefit.

## Correctness and regressions

- Preserve upstream's locked scope/backup sight tests unchanged. Add seven
  database-independent Glock tests covering exact stats, alternative light
  placement and rejection when the suppressor rail is unavailable. Both
  implementations pass these Glock tests and the real snapshot replay:
  ergonomics 96.5, weight 1.310 kg, recoil 234/195.
- Final candidate fixture suite: 259 passed, 54 skipped. Current main plus the
  common Glock fixture: 248 passed, 54 skipped. Selected real M4A1 tests,
  including the locked Vudu/MPR45/RMR/SRO cases: 26 passed on each version.
- Run 29 constraint scenarios. Matching completes 26 and reports three expected
  infeasible cases. Main completes 25, reports the same three infeasible cases,
  and returns a partial curve at 30 seconds with unpriced items enabled;
  matching completes that case in 7.874 seconds.
- All returned builds pass independent slot capacity, required-slot, blocker,
  parent activation/order, connectivity, statistics, price and hard-limit checks.
  These cover 98 measured Explore calls, 12 warmups and 56 auxiliary calls.
- Audit 324 unique candidate exports against main's static rows. Ten physically
  valid builds are excluded by main's global item mutex for AX-15 handguards and
  gas blocks. AX-15 blocks slots on an unused A2 20-inch barrel, while the actual
  gas block is on a selected 10.3-inch barrel. Matching retains these placements;
  a new in-memory Explore regression captures the inactive-parent case.
- That new slot regression and the PR's two TED retry/boundary regressions fail
  on current main and pass on the candidate. They are separate diagnostic
  counterexamples, not failures in main's existing test suite.
- Black and flake8 pass. Mypy reports the same 80 existing diagnostics in nine
  files on both versions, after normalizing shifted line numbers. No frontend
  verification was performed.

## Quality limits

The curves and tied prices are not identical. At eight fixed ordinary-ergo
floors, the recoil objective agrees in every case; three fixed-ergo price
objectives also agree. At nine fixed TED floors, five recoil results agree,
one improves and three worsen. A trace confirms that an existing global TED
tangent excludes a main build with TED 1.01 that satisfies its zero floor, even
though the build passes the candidate's structural rows and matching check.
The shared nonlinear approximation remains path-dependent and does not prove
global TED optimality.

Different minimum-recoil selections move the Explore sampling grid. In the
recoil/price curve, seven of eight coordinates agree, but the minimum-recoil
point costs 457,821 RUB on main versus 472,369 RUB with matching (+3.2%). Direct
recoil-only diagnostics also show varying tied prices, including +49.4% at an
ergo floor of 40 with Explore's price cleanup disabled. The tiny price tiebreak
is not a guarantee of minimum price among equal-recoil selections. Do not claim
uniform frontier or price improvement from the latency results.

Retain one selection per item ID and require an acyclic compatibility graph.
This study covers M4A1, Glock and controlled fixtures, not every weapon.
