"""Compare complete Explore event payloads and timings on two backend checkouts.

Keep persistent workers and alternate serial A/B requests against one read-only
SQLite snapshot. Compare every emitted field except metrics, processing_ms and
prepare_ms; preserve list order and full outputs in samples.jsonl as they arrive.
Partial curves are recorded, and any unequal output makes the command fail.
"""

import argparse
from dataclasses import asdict
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def semantic(value):
    if isinstance(value, dict):
        return {
            key: semantic(item) for key, item in value.items() if key not in ("metrics", "processing_ms", "prepare_ms")
        }
    if isinstance(value, list):
        return [semantic(item) for item in value]
    return value


def emit(value, stream=sys.stdout):
    stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
    stream.flush()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def database_url(path):
    return f"sqlite:///{path.resolve().as_uri()}?mode=ro&uri=true"


def case_definitions(path):
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("--cases-json must contain a nonempty array")
    json.dumps(cases, allow_nan=False)
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"id", "weapon", "steps", "tradeoff", "params"}:
            raise ValueError("Each case requires exactly id, weapon, steps, tradeoff and params")
        if not isinstance(case["id"], str) or not case["id"] or case["id"] in seen:
            raise ValueError("Case IDs must be unique nonempty strings")
        seen.add(case["id"])
        if not isinstance(case["weapon"], str) or not case["weapon"]:
            raise ValueError(f"{case['id']}: weapon must be an existing item ID")
        if type(case["steps"]) is not int or not 10 <= case["steps"] <= 81:
            raise ValueError(f"{case['id']}: steps must be an integer in [10, 81]")
        if case["tradeoff"] not in ("price", "recoil", "ergo") or not isinstance(case["params"], dict):
            raise ValueError(f"{case['id']}: invalid tradeoff or params")
    return cases


def worker(args):
    # Reserve the original pipe before native imports can write to stdout.
    sys.stdout.flush()
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.path.insert(0, str(args.backend.resolve()))
    import numpy
    import scipy
    import sqlalchemy
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from models_items import Item
    from optimizer import explore, milp
    from optimizer.solver import OptimizeParams

    if explore.EXPLORE_TIME_LIMIT_SECONDS != 30 or milp.SOLVE_TIME_LIMIT_SECONDS != 30:
        raise ValueError("Both backends must retain the production 30-second Explore and solver budgets")
    if explore.EXPLORE_WORKERS != args.workers:
        raise ValueError("The backend did not use the requested Explore worker count")
    cases = json.loads(sys.stdin.readline())["cases"]
    params_by_id = {case["id"]: OptimizeParams(**case["params"]) for case in cases}
    engine = create_engine(database_url(args.database))
    try:
        with Session(engine) as db:
            for case in cases:
                item = db.get(Item, case["weapon"])
                if item is None or not item.is_weapon:
                    raise ValueError(f"{case['id']}: unknown weapon ID {case['weapon']!r}")
        sources = sorted((args.backend / "optimizer").glob("*.py"))
        sources += [args.backend / name for name in ("stats.py", "compatibility.py", "database.py", "config.py")]
        sources += sorted(args.backend.glob("models_*.py"))
        emit(
            {
                "python": sys.version,
                "numpy": numpy.__version__,
                "scipy": scipy.__version__,
                "sqlalchemy": sqlalchemy.__version__,
                "workers": explore.EXPLORE_WORKERS,
                "explore_budget_seconds": explore.EXPLORE_TIME_LIMIT_SECONDS,
                "solver_budget_seconds": milp.SOLVE_TIME_LIMIT_SECONDS,
                "source_hashes": {
                    path.relative_to(args.backend).as_posix(): sha256(path) for path in sources if path.is_file()
                },
                "cases": [{**case, "params": asdict(params_by_id[case["id"]])} for case in cases],
            },
            protocol,
        )
        cases_by_id = {case["id"]: case for case in cases}
        for line in sys.stdin:
            case = cases_by_id[json.loads(line)["case_id"]]
            params = OptimizeParams(**case["params"])
            gc.collect()
            with Session(engine) as db:
                started = time.perf_counter()
                events = list(
                    explore.explore_weapon_stream(db, case["weapon"], params, case["tradeoff"], case["steps"])
                )
                elapsed = (time.perf_counter() - started) * 1000
            if not events or events[-1].get("type") != "result":
                raise RuntimeError(f"{case['id']}: Explore did not emit a final result")
            output = semantic(events)
            payload = json.dumps(output, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            result = events[-1]["data"]
            emit(
                {
                    "case_id": case["id"],
                    "wall_ms": elapsed,
                    "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                    "complete": result["complete"],
                    "status": result["status"],
                    "events": output,
                },
                protocol,
            )
    finally:
        engine.dispose()
        protocol.close()


def read_worker(process, revision, output):
    line = process.stdout.readline()
    if not line:
        raise RuntimeError(f"{revision} worker stopped; inspect {output / (revision + '.stderr.log')}")
    try:
        return json.loads(line)
    except ValueError as exc:
        raise RuntimeError(f"Invalid {revision} worker output; inspect {output / (revision + '.stderr.log')}") from exc


def cleanup(processes, logs):
    for process in processes.values():
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        finally:
            if process.stdout is not None:
                process.stdout.close()
    for log in logs:
        log.close()


def controller(args, cases):
    args.output.mkdir(parents=True, exist_ok=False)
    env = {
        **os.environ,
        "EFTFORGE_EXPLORE_WORKERS": str(args.workers),
        "PYTHONHASHSEED": "0",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "IP_HASH_SECRET": "explore-output-benchmark-only",
        "ADMIN_API_KEY": "explore-output-benchmark-only",
        "DISABLE_BG_MIGRATE": "1",
        "DATABASE_URL": database_url(args.database),
    }
    metadata = {
        "mode": "full_explore_output_comparison",
        "database": str(args.database.resolve()),
        "database_sha256": sha256(args.database),
        "cases_file_sha256": sha256(args.cases_json),
        "cases": cases,
        "runs": args.runs,
        "warmup_pairs_per_case": args.warmups,
        "workers": args.workers,
        "production_budget_seconds": 30,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "thread_env": {
            key: env[key] for key in ("PYTHONHASHSEED", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
        },
        "backends": {"baseline": str(args.baseline.resolve()), "candidate": str(args.candidate.resolve())},
    }
    write_json(args.output / "metadata.json", metadata)
    processes, logs, records, mismatches = {}, [], [], []
    try:
        for revision, backend in (("baseline", args.baseline), ("candidate", args.candidate)):
            log = (args.output / f"{revision}.stderr.log").open("w", encoding="utf-8")
            logs.append(log)
            processes[revision] = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    "--backend",
                    str(backend.resolve()),
                    "--database",
                    str(args.database.resolve()),
                    "--workers",
                    str(args.workers),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                encoding="utf-8",
                env=env,
            )
            emit({"cases": cases}, processes[revision].stdin)
            metadata[revision] = read_worker(processes[revision], revision, args.output)
            write_json(args.output / "metadata.json", metadata)
        if metadata["baseline"]["cases"] != metadata["candidate"]["cases"]:
            raise RuntimeError("Backends normalized the requested parameters differently; inspect metadata.json")
        with (args.output / "samples.jsonl").open("w", encoding="utf-8") as stream:
            for round_id in range(args.warmups + args.runs):
                for case_index, case in enumerate(cases):
                    pair = {}
                    order = ("baseline", "candidate") if (round_id + case_index) % 2 == 0 else ("candidate", "baseline")
                    for revision in order:
                        emit({"case_id": case["id"]}, processes[revision].stdin)
                        row = {
                            **read_worker(processes[revision], revision, args.output),
                            "revision": revision,
                            "round": round_id,
                            "phase": "warmup" if round_id < args.warmups else "measured",
                        }
                        if row["case_id"] != case["id"]:
                            raise RuntimeError(f"{revision} worker returned the wrong case; inspect its stderr log")
                        emit(row, stream)
                        records.append({key: value for key, value in row.items() if key != "events"})
                        pair[revision] = row
                        emit({key: value for key, value in row.items() if key != "events"})
                    if (
                        pair["baseline"]["sha256"] != pair["candidate"]["sha256"]
                        or pair["baseline"]["events"] != pair["candidate"]["events"]
                    ):
                        mismatch = {"case_id": case["id"], "round": round_id, "phase": row["phase"]}
                        mismatches.append(mismatch)
                        print(f"Output mismatch: {mismatch}; full evidence is in samples.jsonl", file=sys.stderr)
        if sha256(args.database) != metadata["database_sha256"]:
            raise RuntimeError("Database changed during the comparison; discard these measurements")
        summary = {
            "requests": len(records),
            "paired_requests": len(records) // 2,
            "complete_requests": sum(row["complete"] for row in records),
            "semantic_mismatches": len(mismatches),
            "mismatches": mismatches,
            "cases": {},
        }
        for case in cases:
            summary["cases"][case["id"]] = {}
            for revision in processes:
                rows = [
                    row
                    for row in records
                    if row["case_id"] == case["id"] and row["revision"] == revision and row["phase"] == "measured"
                ]
                summary["cases"][case["id"]][revision] = {
                    "n": len(rows),
                    "complete_runs": sum(row["complete"] for row in rows),
                    "wall_ms_median": statistics.median(row["wall_ms"] for row in rows),
                    "wall_ms_range": [min(row["wall_ms"] for row in rows), max(row["wall_ms"] for row in rows)],
                    "statuses": [row["status"] for row in rows],
                }
        write_json(args.output / "summary.json", summary)
        if mismatches:
            raise RuntimeError(f"{len(mismatches)} output mismatches; inspect summary.json and samples.jsonl")
    finally:
        cleanup(processes, logs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--backend", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--database", type=Path, required=True, help="Frozen, checkpointed SQLite snapshot")
    parser.add_argument("--cases-json", type=Path, help="Case array with id, weapon ID, steps, tradeoff and params")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if args.runs < 1 or args.warmups < 0 or args.workers < 1:
        parser.error("Require --runs >= 1, --warmups >= 0 and --workers >= 1")
    if not args.database.is_file():
        parser.error("--database must be an existing file")
    wal = Path(str(args.database) + "-wal")
    if wal.exists() and wal.stat().st_size:
        parser.error("Use a checkpointed snapshot without a nonempty WAL file")
    backends = [args.backend] if args.worker else [args.baseline, args.candidate]
    for backend in backends:
        if backend is None or not all(
            (backend / name).is_file() for name in ("optimizer/explore.py", "optimizer/solver.py")
        ):
            parser.error("Each backend must contain optimizer/explore.py and optimizer/solver.py")
    if args.worker:
        worker(args)
        return
    if args.output is None or args.output.exists() or args.cases_json is None or not args.cases_json.is_file():
        parser.error("Require --cases-json as an existing file and --output as a new directory")
    try:
        cases = case_definitions(args.cases_json)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    controller(args, cases)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Comparison failed: {exc}", file=sys.stderr)
        sys.exit(1)
