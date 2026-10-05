"""Rootless native PostgreSQL validation alongside existing Neo4j and browser.

This installs no system packages, edits no production configuration and stops
its private PostgreSQL instance after testing. Run only from the validation venv.
"""
from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import socket
import signal


BASE = Path("/home/ubuntu/validation/memory-graph-20261005")


def repair_neo4j(run_id):
    """Replace only the known isolated validation unit, never a production unit."""
    root = BASE / "native/neo4j-community-5.26.31"
    config = root / "conf/neo4j.conf"
    text = config.read_text()
    if "127.0.0.1:17687" not in text or "# Isolated, loopback-only Graphiti validation instance." not in text:
        raise RuntimeError("refuse to change an unrecognized Neo4j configuration")
    original_unit = "memory-neo4j-validation-direct-20261005.service"
    loaded = subprocess.run(["systemctl", "--user", "show", original_unit, "--property=LoadState", "--value"],
                            text=True, capture_output=True, timeout=15, check=True)
    if loaded.stdout.strip() != "not-found":
        command(["systemctl", "--user", "stop", original_unit], timeout=40)
    config.write_text(text.replace("-XX:MaxMetaspaceSize=128m", "-XX:MaxMetaspaceSize=192m"))
    unit = "memory-neo4j-validation-repaired-" + run_id
    java = BASE / "native/jdk-21.0.12.1+1-jre/bin/java"
    command(["systemd-run", "--user", "--quiet", "--collect", "--unit=" + unit,
        "--property=MemoryMax=768M", "--property=MemorySwapMax=0", "--property=RuntimeMaxSec=900",
        "--working-directory=" + str(root), java, "-cp",
        ":".join(str(root / p) + "/*" for p in ("plugins", "conf", "lib")),
        "-XX:+UseG1GC", "-XX:MaxMetaspaceSize=192m", "-XX:ReservedCodeCacheSize=32m",
        "-XX:MaxDirectMemorySize=32m", "-XX:ActiveProcessorCount=1", "-Dlog4j2.disable.jmx=true",
        "--add-opens=java.base/java.nio=ALL-UNNAMED", "--add-opens=java.base/java.io=ALL-UNNAMED",
        "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED", "--enable-native-access=ALL-UNNAMED",
        "-Dfile.encoding=UTF-8", "-Xms64m", "-Xmx192m", "org.neo4j.server.CommunityEntryPoint",
        "--home-dir=" + str(root), "--config-dir=" + str(root / "conf"), "--console-mode"], timeout=30)
    return unit


def command(argv, *, cwd=None, env=None, timeout=180):
    return subprocess.run([str(x) for x in argv], cwd=cwd, env=env,
                          check=True, timeout=timeout)


def prepare(prefix):
    downloads = prefix / "packages" / str(time.time_ns())
    downloads.mkdir(parents=True, exist_ok=True)
    packages = ["postgresql-16", "postgresql-client-16", "postgresql-16-pgvector", "libpq5"]
    command(["apt-get", "download", *packages], cwd=downloads)
    receipt = []
    for package in packages:
        files = sorted(downloads.glob(package + "_*.deb"))
        if not files:
            raise RuntimeError("downloaded package missing: " + package)
        filename = files[-1]
        command(["dpkg-deb", "--extract", filename, prefix / "root"])
        version = subprocess.check_output(["dpkg-deb", "--field", str(filename), "Version"], text=True).strip()
        receipt.append({"package": package, "version": version, "filename": filename.name})
    (prefix / "packages.json").write_text(json.dumps(receipt, indent=2) + "\n")


def sample_resources(stop, output, phase):
    import psutil
    with output.open("a") as handle:
        while not stop.is_set():
            processes = []
            for process in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
                try:
                    cmd = " ".join(process.info["cmdline"] or [])
                    if any(x in cmd for x in ("neo4j", "postgres", "chrome", "chromium", "agent-memory-", "fastembed", "memory/local_retrieval.py")):
                        processes.append({"pid": process.pid, "name": process.info["name"],
                                          "rss_bytes": process.info["memory_info"].rss})
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            memory, swap = psutil.virtual_memory(), psutil.swap_memory()
            handle.write(json.dumps({"recorded_at": time.time(), "phase": phase["name"], "available_bytes": memory.available,
                "swap_used_bytes": swap.used, "swap_in_bytes": swap.sin, "swap_out_bytes": swap.sout,
                "cpu_seconds": psutil.cpu_times()._asdict(), "processes": processes,
                "aggregate_rss_bytes": sum(p["rss_bytes"] for p in processes)}) + "\n")
            handle.flush()
            stop.wait(.5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-db", action="store_true")
    parser.add_argument("--measure-models", action="store_true")
    parser.add_argument("--repair-neo4j", action="store_true")
    args = parser.parse_args()
    if os.geteuid() == 0 or "content-agent-prod-venv" in sys.prefix:
        raise RuntimeError("run as the regular owner in the separate validation venv")
    if Path(sys.prefix).resolve() != (BASE / "venv").resolve():
        raise RuntimeError("unexpected Python environment; refuse native preparation")
    root = Path(__file__).resolve().parents[1]
    prefix = BASE / "native-postgres"
    prefix.mkdir(parents=True, exist_ok=True)
    if args.prepare_db:
        prepare(prefix)
    native = prefix / "root"
    binaries = native / "usr/lib/postgresql/16/bin"
    share = native / "usr/share/postgresql/16"
    if not (binaries / "postgres").is_file() or not (share / "extension/vector.control").is_file():
        raise RuntimeError("native PostgreSQL and pgvector packages are not prepared")
    # Ubuntu binaries discover their relative data directory. Explicitly pass
    # initdb's share path as well, so no system database installation is used.
    run_id = str(time.time_ns())
    run = prefix / ("run-" + run_id)
    run.mkdir(mode=0o700)
    socket_dir, data = run / "socket", run / "data"
    socket_dir.mkdir(mode=0o700)
    env = dict(os.environ, GRAPHITI_TELEMETRY_ENABLED="false", SEMAPHORE_LIMIT="1")
    env["LD_LIBRARY_PATH"] = str(native / "usr/lib/x86_64-linux-gnu")
    command([binaries / "initdb", "-D", data, "-L", share, "--encoding=UTF8", "--no-locale",
             "--auth-local=peer", "--auth-host=reject", "--username=" + getpass.getuser()], env=env)
    stop, server, neo4j_unit = threading.Event(), None, None
    phase = {"name": "database_start"}
    monitor = threading.Thread(target=sample_resources, args=(stop, run / "resources.jsonl", phase), daemon=True)
    monitor.start()
    receipt = {"production_deployed": False, "full_memory_plan_complete": False,
               "run_dir": str(run), "native_postgres": "STARTING", "models": "NOT_RUN"}
    receipt["deployment_resource_gate"] = "NOT_EVALUATED; functional smoke measurement only"
    try:
        if args.repair_neo4j:
            neo4j_unit = repair_neo4j(run_id)
            receipt["neo4j_budget"] = {"initial_heap_mb": 64, "maximum_heap_mb": 192,
                                        "metaspace_mb": 192, "cgroup_memory_max_mb": 768}
            deadline = time.monotonic() + 45
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", 17687), timeout=1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("repaired validation Neo4j did not start")
                    time.sleep(.25)
        with (run / "postgres.log").open("w") as log:
            server = subprocess.Popen(["systemd-run", "--user", "--quiet", "--pipe", "--wait", "--collect",
                "--unit=memory-postgres-validation-" + run_id,
                "--setenv=LD_LIBRARY_PATH=" + env["LD_LIBRARY_PATH"],
                "--property=MemoryMax=384M", "--property=MemorySwapMax=0", "--property=RuntimeMaxSec=900",
                str(binaries / "postgres"), "-D", str(data), "-c", "listen_addresses=",
                "-c", "unix_socket_directories=" + str(socket_dir), "-c", "shared_buffers=32MB",
                "-c", "work_mem=2MB", "-c", "max_connections=10",
                "-c", "dynamic_library_path=" + str(native / "usr/lib/postgresql/16/lib")],
                env=env, stdout=log, stderr=subprocess.STDOUT)
            import psycopg
            # Use a private Unix socket with peer authentication, never TCP.
            dsn = "dbname=postgres user=" + getpass.getuser() + " host=" + str(socket_dir) + " connect_timeout=2"
            deadline = time.monotonic() + 45
            while True:
                if server.poll() is not None:
                    raise RuntimeError("private PostgreSQL exited: " + (run / "postgres.log").read_text()[-2000:])
                try:
                    with psycopg.connect(dsn) as db:
                        db.execute("CREATE EXTENSION vector")
                        receipt["postgres_version"] = db.execute("SELECT version()").fetchone()[0]
                        receipt["vector_version"] = db.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
                    break
                except psycopg.OperationalError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.25)
            receipt["native_postgres"] = "RUNNING"
            env["MEMORY_TEST_POSTGRES_DSN"] = dsn
            phase["name"] = "provider_tests"
            tests = subprocess.Popen([sys.executable, "scripts/validate_memory_provider_batch.py", "--neo4j-validation",
                                      "--output-dir", str(run / "tests")], cwd=root, env=env, start_new_session=True)
            try:
                tests.wait(timeout=300)
            except subprocess.TimeoutExpired:
                os.killpg(tests.pid, signal.SIGTERM)
                try:
                    tests.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(tests.pid, signal.SIGKILL)
                    tests.wait(timeout=5)
                raise
            receipt["tests_returncode"] = tests.returncode
            if tests.returncode:
                raise RuntimeError("native provider tests failed; see tests/receipt.json")
            receipt["native_postgres"] = "PASSED"
            if args.measure_models:
                # This model worker is independent of the database/SDK environment.
                # Reuse the installed, read-only production model environment;
                # run candidate worker source without installing into that venv.
                model_python = Path("/home/ubuntu/content-agent-prod-venv-connectome-ffc4c726-c7d266d4-d2f4fd8c/bin/python")
                if not model_python.is_file():
                    raise RuntimeError("existing model Python environment is unavailable")
                spec = importlib.util.spec_from_file_location("native_model_measurement", root / "src/agent_runtime/memory/local_retrieval.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                import psutil
                if psutil.virtual_memory().available < 1100 * 1024 * 1024:
                    raise RuntimeError("insufficient free host RAM for bounded real model measurement")
                models = module.LocalRetrievalModels(run, worker_python=model_python,
                                                    metrics_path=run / "models.jsonl")
                for cycle in range(3):
                    phase["name"] = f"models_{cycle + 1}_embed"
                    vectors = models.embed(["长期智能体记忆必须保留原始证据和修订历史。" * 40])
                    phase["name"] = f"models_{cycle + 1}_query"
                    query = models.embed(["如何保留记忆证据？"], query=True)
                    phase["name"] = f"models_{cycle + 1}_rerank"
                    ranking = models.rerank("如何保留记忆证据？", ["保留原始来源与修订历史。" * 40, "苹果派需要烘焙。" * 40])
                    if len(vectors) != 1 or len(query) != 1 or len(ranking) != 2 or not ranking[0] > ranking[1]:
                        raise RuntimeError("bounded real model measurement returned invalid output/ranking")
                receipt.update(models="PASSED", embedding_model=models.model_id, dimensions=len(vectors[0]),
                               reranker=models.reranker, model_worker_python=str(model_python),
                               model_measurements=[json.loads(line) for line in (run / "models.jsonl").read_text().splitlines()])
    except Exception as exc:
        receipt.update(status="FAILED", error_type=type(exc).__name__, error=str(exc))
        raise
    else:
        receipt["status"] = "PASSED"
    finally:
        phase["name"] = "cleanup"
        if server is not None:
            subprocess.run([str(binaries / "pg_ctl"), "-D", str(data), "stop", "-m", "fast", "-w", "-t", "30"],
                           env=env, timeout=40, check=False)
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.terminate()
                server.wait(timeout=10)
        if neo4j_unit is not None:
            subprocess.run(["systemctl", "--user", "stop", neo4j_unit], timeout=40, check=False)
        stop.set()
        monitor.join(timeout=3)
        samples = [json.loads(line) for line in (run / "resources.jsonl").read_text().splitlines()]
        if samples:
            receipt["resources"] = {"samples": len(samples),
                "minimum_host_available_bytes": min(s["available_bytes"] for s in samples),
                "maximum_aggregate_rss_bytes": max(s["aggregate_rss_bytes"] for s in samples),
                "swap_increase_bytes": samples[-1]["swap_used_bytes"] - samples[0]["swap_used_bytes"],
                "observed_process_names": sorted({p["name"] for s in samples for p in s["processes"]}),
                "rss_shared_pages_may_be_counted_more_than_once": True}
            receipt["resource_phases"] = {}
            for name in dict.fromkeys(s["phase"] for s in samples):
                selected = [s for s in samples if s["phase"] == name]
                receipt["resource_phases"][name] = {
                    "samples": len(selected), "minimum_host_available_bytes": min(s["available_bytes"] for s in selected),
                    "swap_used_delta_bytes": selected[-1]["swap_used_bytes"] - selected[0]["swap_used_bytes"],
                    "swap_in_delta_bytes": selected[-1]["swap_in_bytes"] - selected[0]["swap_in_bytes"],
                    "swap_out_delta_bytes": selected[-1]["swap_out_bytes"] - selected[0]["swap_out_bytes"]}
        (run / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        (prefix / "latest-run.json").write_text(json.dumps({"run_dir": str(run), "receipt": receipt}, indent=2) + "\n")
        print(json.dumps(receipt))


if __name__ == "__main__":
    main()
