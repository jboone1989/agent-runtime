"""Validate this candidate in a dedicated environment; never deploy production.

Run with the memory validation venv's Python, optionally --prepare to install
the locked provider extras into that environment. Native endpoints are opt-in
environment variables. An absent endpoint remains NOT_RUN, never a pass.
"""
import argparse
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--neo4j-validation", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("/home/ubuntu/validation/memory-graph-20261005/provider-batch"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.prepare:
        # Explicitly reject installing extras into the active Ferro environment.
        if "content-agent-prod-venv" in sys.prefix:
            raise RuntimeError("use the separate memory validation environment")
        subprocess.run([sys.executable, "-m", "pip", "install", "-e",
                        str(root) + "[dev,memory,memory-graph,memory-documents,memory-content]"], check=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    xml = args.output_dir / "tests.xml"
    env = dict(os.environ, GRAPHITI_TELEMETRY_ENABLED="false", SEMAPHORE_LIMIT="1")
    if args.neo4j_validation:
        base = Path("/home/ubuntu/validation/memory-graph-20261005")
        env.update(MEMORY_TEST_NEO4J_URI="bolt://127.0.0.1:17687",
                   MEMORY_TEST_NEO4J_PASSWORD_FILE=str(base / "neo4j-password"),
                   MEMORY_TEST_GRAPH_VECTORS=str(base / "integration-vectors.json"))
    result = subprocess.run([sys.executable, "-m", "pytest", "-q",
             "tests/test_graph_projection.py", "tests/test_graph_projection_neo4j.py",
             "tests/test_topic_hierarchy.py", "tests/test_document_hierarchy.py",
             "tests/test_content_index_worker.py", "tests/test_postgres_content_integration.py",
             "tests/test_memory_session.py", "tests/test_memory_formation_pipeline.py",
             "--junitxml=" + str(xml)], cwd=root, env=env)
    suites = ET.parse(xml).getroot() if xml.exists() else None
    tests = [] if suites is None else list(suites.iter("testcase"))
    package_versions = {}
    for package in ("graphiti-core", "llama-index-core", "psycopg", "pgvector", "jieba"):
        try:
            package_versions[package] = version(package)
        except PackageNotFoundError:
            package_versions[package] = "NOT_INSTALLED"
    receipt = {"recorded_at": datetime.now(timezone.utc).isoformat(), "returncode": result.returncode,
               "tests": len(tests), "skipped": sum(t.find("skipped") is not None for t in tests),
               "failures": sum(t.find("failure") is not None or t.find("error") is not None for t in tests),
               "packages": package_versions,
               "native_postgres": "REQUESTED" if env.get("MEMORY_TEST_POSTGRES_DSN") else "NOT_RUN",
               "native_neo4j": "REQUESTED" if env.get("MEMORY_TEST_NEO4J_URI") else "NOT_RUN",
               "production_deployed": False, "full_memory_plan_complete": False}
    (args.output_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
