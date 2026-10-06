# Explore model preparation

Reuse the exact placement topology and item-to-slot map inside one prepared
Explore request. Clone each solve's mutable placement state, keep its dynamic
constraints and learned cuts separate, and initialize the shared graph under a
lock so parallel shallow copies reuse it too.

Compile sparse rows through CSR before converting to the same CSC representation
HiGHS consumes. Keep zero filtering, empty rows, coefficient order, bounds,
append invalidation and column expansion unchanged. Differential regressions
compare CSC values, indices, pointers, dtypes and bounds against the original
coordinate conversion byte for byte.

Keep every formula, objective, constraint, grid sample, anchor, refinement limit,
solver option, tolerance and deadline unchanged. Keep candidate order unchanged
because existing statistic accumulation can depend on it. Direct calls without
prepared inputs retain fresh topology construction.

## Local measurements

Compare the original working tree at `a605462cdb5b867634b3b1b042f63ca05cf1c68b`
against these changes on October 6, 2026. Use the same read-only local database,
Python 3.12.10, SciPy 1.18.1, NumPy 2.5.3, Windows 11 and 16 logical CPUs.
Keep `PYTHONHASHSEED=0` and BLAS/OpenMP thread environment variables at 1.
Retain the production 30-second request budget. Time the direct streaming
generator, including preparation and output construction, excluding imports,
pre-run garbage collection and post-run validation. Alternate baseline and
candidate runs using persistent workers.

[Case definitions](optimizer_setup_cases.json) cover all three tradeoffs,
loaded ammunition, overswing prevention, TrueErgo with equipment/strength
modifiers, MOA/sighting limits, and both shallow and deep attachment graphs.
[Recorded results](results/optimizer_setup_summary.json) include source and
database hashes, runtime versions, timings, output digests and audit counts.

With one Explore worker, three measured runs per revision after one warm-up pair
reduced combined model/matrix preparation by 53.1% in aggregate. Whole-request
time decreased by 1.7% in aggregate. All 32 pairs, including warm-ups, retained
the same selections, stats, prices, bounds, statuses and native solve sequence.
All 48 measured requests completed. Frozen-output auditing checked 990 selections
with no model or placement violations. Sorting selections deliberately reproduced
18 existing statistic-rounding differences; actual order and reported stats
matched between revisions.

With the default eight Explore workers, two measured runs per revision after
one warm-up pair produced these median whole-request times:

| Case | Original ms | Updated ms | Reduction |
| --- | ---: | ---: | ---: |
| M4A1, 10 steps | 1108.6 | 1037.1 | 6.4% |
| HK416, 81 steps | 1789.0 | 1603.0 | 10.4% |
| AK-101, overswing prevention | 1265.4 | 1218.3 | 3.7% |
| M4A1, TrueErgo | 4984.6 | 5002.3 | -0.4% |
| SR-25, price versus ergonomics | 223.9 | 175.2 | 21.8% |
| SA58, price versus recoil | 167.6 | 147.9 | 11.8% |
| PPSh-41 | 7.1 | 6.7 | 6.8% |
| SR-25, MOA and sighting limits | 523.3 | 449.6 | 14.1% |

Aggregate parallel time decreased by 4.3%. All 24 pairs, including warm-ups,
matched complete emitted output after excluding timing/diagnostic metrics.
That comparison retains selected-item order, progress, per-item TrueErgo
contributions, price details and solver termination data. All 48 requests
completed. Two repetitions and sub-millisecond shallow-case changes provide
limited timing precision; these figures describe this snapshot and machine,
not an SLA or a universal speed guarantee. HiGHS search still dominates expensive
cases, particularly TrueErgo.

Backend verification passed 334 tests with three skips, followed by all five new
model-input regressions. Serial Flake8 and Black's formatting API passed for the
changed Python files. The reusable full-output comparison also passed a PPSh-41
smoke run. The Black CLI stalled under the sandbox, so
formatting was checked with `format_file_contents` at the repository's 120-column
limit. Frontend verification remains with the maintainer.

## Reproduce

Use a separate checkout for the baseline and the same database snapshot. From
the repository root, run the instrumented serial comparison:

```powershell
$env:EFTFORGE_EXPLORE_WORKERS = '1'
.\backend\venv\Scripts\python.exe backend/benchmarks/explore_ab.py --baseline C:/path/to/baseline/backend --candidate backend --database backend/tarkov.db --cases-json backend/benchmarks/optimizer_setup_cases.json --output C:/path/to/new-serial-results --runs 3 --warmups 1
```

Run the full-output comparison with eight workers:

```powershell
.\backend\venv\Scripts\python.exe backend/benchmarks/explore_output_ab.py --baseline C:/path/to/baseline/backend --candidate backend --database backend/tarkov.db --cases-json backend/benchmarks/optimizer_setup_cases.json --output C:/path/to/new-parallel-results --runs 2 --warmups 1 --workers 8
```

Audit the frozen serial results without further MILP calls:

```powershell
.\backend\venv\Scripts\python.exe backend/benchmarks/audit_v1_expanded.py --backend backend --database backend/tarkov.db --run C:/path/to/new-serial-results --output C:/path/to/model-audit.json
```
