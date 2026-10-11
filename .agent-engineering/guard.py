"""A tiny standalone architecture + behavior-test gate. Python stdlib only.

This module is also vendored into initialized projects, so their CI has no
runtime dependency on a not-yet-published package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

VERSION = "0.4.1"
PY_MODULE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*$")
TS_MODULE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*(/[a-zA-Z_][a-zA-Z0-9_-]*)*$")
PACKAGE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*$")
SOURCE = re.compile(r"^[A-Za-z0-9_./-]+$")


class PolicyError(ValueError):
    """An invalid, inconsistent or incomplete project policy."""


def _source_path(value: str) -> str:
    if not isinstance(value, str) or not SOURCE.fullmatch(value):
        raise PolicyError("source must be a simple relative path")
    result = Path(value)
    # Leading "/" is absolute on POSIX even though Windows Path.is_absolute says otherwise.
    if result.is_absolute() or value.startswith("/") or any(part in (".", "..") for part in value.split("/")):
        raise PolicyError("source cannot leave the project directory")
    return value.rstrip("/")


def validate_policy(value: object) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise PolicyError("policy must be an object with schema_version=1")
    lang = value.get("language")
    if lang not in ("python", "typescript"):
        raise PolicyError("language must be python or typescript")
    if lang == "python" and not (isinstance(value.get("package"), str) and PACKAGE.fullmatch(value["package"])):
        raise PolicyError("Python requires a valid dotted import package")
    if lang == "typescript":
        _source_path(value.get("source"))
    if not isinstance(value.get("check_cycles", False), bool):
        raise PolicyError("check_cycles must be a boolean")
    timeout = value.get("command_timeout_seconds", 900)
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise PolicyError("command_timeout_seconds must be an integer between 1 and 3600")
    rules = value.get("forbidden")
    if not isinstance(rules, list):
        raise PolicyError("forbidden must be a list of module pairs")
    seen = set()
    module_pattern = PY_MODULE if lang == "python" else TS_MODULE
    for pair in rules:
        if (not isinstance(pair, list) or len(pair) != 2 or
                any(not isinstance(m, str) or not module_pattern.fullmatch(m) for m in pair) or pair[0] == pair[1]):
            raise PolicyError("forbidden must contain distinct [source, target] module pairs")
        if tuple(pair) in seen:
            raise PolicyError("duplicate forbidden rule: " + repr(pair))
        seen.add(tuple(pair))
    modules = value.get("modules")
    allowed = value.get("allowed_dependencies")
    if modules is not None or allowed is not None:
        if not isinstance(modules, list) or len(modules) < 2 or not all(
                isinstance(m, str) and module_pattern.fullmatch(m) for m in modules):
            raise PolicyError("modules must contain at least two valid module names")
        if len(modules) != len(set(modules)):
            raise PolicyError("duplicate declared module")
        if not isinstance(allowed, list):
            raise PolicyError("allowed_dependencies must be a list")
        for pair in allowed:
            if (not isinstance(pair, list) or len(pair) != 2 or
                    pair[0] == pair[1] or any(m not in modules for m in pair)):
                raise PolicyError("allowed_dependencies contains an undeclared or invalid edge")
        if len(allowed) != len({tuple(pair) for pair in allowed}):
            raise PolicyError("duplicate allowed dependency")
        if any(pair in allowed for pair in rules):
            raise PolicyError("same dependency cannot be both allowed and forbidden")
        coverage = value.get("module_coverage", "top_level")
        if coverage not in ("top_level", "off"):
            raise PolicyError("module_coverage must be top_level or off")
        unmanaged = value.get("unmanaged_modules", [])
        if (not isinstance(unmanaged, list) or any(
                not isinstance(m, str) or not module_pattern.fullmatch(m) for m in unmanaged)):
            raise PolicyError("unmanaged_modules must be module names")
        if len(unmanaged) != len(set(unmanaged)):
            raise PolicyError("duplicate unmanaged module")
        if set(unmanaged) & set(modules):
            raise PolicyError("a module cannot be declared and unmanaged")
    elif not rules:
        raise PolicyError("configure forbidden pairs or a modules allowlist")
    strict = value.get("strict", False)
    if type(strict) is not bool:
        raise PolicyError("strict must be a boolean")
    entries = value.get("relaxations", [])
    if not isinstance(entries, list):
        raise PolicyError("relaxations must be a list")
    previous = "0" * 64
    for entry in entries:
        if not isinstance(entry, dict):
            raise PolicyError("each relaxation must be an object")
        try:
            stamp = datetime.fromisoformat(entry.get("date_utc", ""))
        except (TypeError, ValueError):
            raise PolicyError("relaxation date_utc must be an ISO 8601 timestamp")
        if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
            raise PolicyError("relaxation date_utc must carry a UTC offset (+00:00 or Z)")
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
            raise PolicyError("relaxation reason must be a non-empty string")
        changes = entry.get("changes")
        if (not isinstance(changes, list) or not changes or
                any(not isinstance(c, str) or not c.strip() for c in changes)):
            raise PolicyError("relaxation changes must be a non-empty list of non-empty strings")
        prev = entry.get("prev_entry_sha256")
        if not isinstance(prev, str) or not re.fullmatch(r"[0-9a-f]{64}", prev):
            raise PolicyError("relaxation prev_entry_sha256 must be 64 lowercase hex characters")
        if prev != previous:
            raise PolicyError("relaxation chain is broken: entries were edited, reordered or deleted")
        previous = _canonical_sha256(entry)
    tests = value.get("tests")
    if not isinstance(tests, list) or any(not isinstance(t, list) or not t or any(not isinstance(s, str) or not s for s in t) for t in tests):
        raise PolicyError("tests must be a list of non-empty argv arrays")
    return value


def load_policy(root: Path) -> dict:
    path = root / ".agent-engineering" / "policy.json"
    try:
        return validate_policy(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read {path}: {exc}") from exc


def forbidden_pairs(policy: dict) -> list[list[str]]:
    pairs = list(policy["forbidden"])
    modules = policy.get("modules")
    if modules is None:
        return pairs
    allowed = {tuple(edge) for edge in policy["allowed_dependencies"]}
    for source in modules:
        for target in modules:
            pair = [source, target]
            if source != target and (source, target) not in allowed and pair not in pairs:
                pairs.append(pair)
    return pairs


def _test_label(argv: list[str]) -> str:
    return json.dumps(argv, separators=(",", ":"), ensure_ascii=False)


def _effective_check_cycles(policy: dict) -> bool:
    if "check_cycles" not in policy:
        return policy.get("language") == "typescript"
    return policy["check_cycles"]


def relaxation_changes(old: dict, new: dict) -> list[str]:
    """Enumerate machine-checkable loosenings from old to new; empty when new is equal or stricter."""
    changes: list[str] = []
    old_forbidden = {tuple(pair) for pair in old.get("forbidden", [])}
    new_forbidden = {tuple(pair) for pair in new.get("forbidden", [])}
    for src, dst in sorted(old_forbidden - new_forbidden):
        changes.append(f"removed_forbidden: {src}:{dst}")
    old_allowed = {tuple(pair) for pair in old.get("allowed_dependencies", [])}
    new_allowed = {tuple(pair) for pair in new.get("allowed_dependencies", [])}
    for src, dst in sorted(old_allowed - new_allowed):
        changes.append(f"removed_allowed: {src}:{dst}")
    for module in sorted(set(new.get("unmanaged_modules", [])) - set(old.get("unmanaged_modules", []))):
        changes.append(f"unmanaged: {module}")
    for module in sorted(set(old.get("modules", [])) - set(new.get("modules", []))):
        changes.append(f"removed_module: {module}")
    if old.get("module_coverage", "top_level") == "top_level" and new.get("module_coverage") == "off":
        changes.append("coverage_off")
    if _effective_check_cycles(old) and not _effective_check_cycles(new):
        changes.append("cycles_disabled")
    old_tests = {_test_label(t) for t in old.get("tests", [])}
    for label in sorted(old_tests - {_test_label(t) for t in new.get("tests", [])}):
        changes.append(f"removed_test: {label}")
    if old.get("strict", False) and not new.get("strict", False):
        changes.append("strict_disabled")
    return sorted(changes)


def _canonical_sha256(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def policy_fingerprint(policy: dict) -> str:
    return _canonical_sha256(policy)


def render_python(policy: dict) -> str:
    package = policy["package"]
    lines = ["[importlinter]", f"root_package = {package}", ""]
    for n, (source, target) in enumerate(forbidden_pairs(policy), start=1):
        lines.extend([
            f"[importlinter:contract:boundary-{n}]",
            f"name = {source} must not depend on {target}",
            "type = forbidden",
            f"source_modules = {package}.{source}",
            f"forbidden_modules = {package}.{target}",
            "",
        ])
    if policy.get("check_cycles", False):
        lines.extend([
            "[importlinter:contract:no-sibling-cycles]",
            "name = Top-level package modules must not form cycles",
            "type = acyclic_siblings",
            f"ancestors = {package}",
            "depth = 0",
            "",
        ])
    return "\n".join(lines)


def render_typescript(policy: dict) -> str:
    source = re.escape(policy["source"])
    rules = [
        {"name": "no-unresolved", "severity": "error", "from": {}, "to": {"couldNotResolve": True}},
    ]
    if policy.get("check_cycles", True):
        rules.insert(0, {"name": "no-circular", "severity": "error", "from": {}, "to": {"circular": True}})
    for left, right in forbidden_pairs(policy):
        left_path, right_path = re.escape(left), re.escape(right)
        rules.append({
            "name": f"no-{left}-to-{right}", "severity": "error",
            "from": {"path": rf"^{source}/{left_path}(?:/|\.)"},
            "to": {"path": rf"^{source}/{right_path}(?:/|\.)"},
        })
    # Type-only imports and unused imports are real source-level dependencies.
    # Without this option, dependency-cruiser discards them after TS compilation.
    return "module.exports = " + json.dumps({
        "forbidden": rules, "options": {"tsPreCompilationDeps": True},
    }, indent=2) + ";\n"


def missing_modules(root: Path, policy: dict) -> list[str]:
    """Prevent a vacuous green check when configured modules do not exist."""
    missing = []
    names = sorted(set(policy.get("modules") or []) | {m for pair in policy["forbidden"] for m in pair})
    if policy["language"] == "python":
        package = Path(*policy["package"].split("."))
        bases = [root / "src" / package, root / package]
        for module in names:
            if not any((base / Path(*module.split("."))).is_dir() or (base / Path(*module.split("."))).with_suffix(".py").is_file() for base in bases):
                missing.append(module)
    else:
        base = root / policy["source"]
        for module in names:
            if not (base / module).is_dir() and not any((base / f"{module}{suffix}").is_file() for suffix in (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts")):
                missing.append(module)
    return missing


def ungoverned_modules(root: Path, policy: dict) -> list[str]:
    """Top-level source modules not governed by strict mode; no runtime imports."""
    if "modules" not in policy or policy.get("module_coverage", "top_level") == "off":
        return []
    if policy["language"] == "python":
        package = Path(*policy["package"].split("."))
        base = next((p for p in (root / "src" / package, root / package) if p.is_dir()), None)
        extensions = (".py",)
        splitter = "."
    else:
        base = root / policy["source"]
        extensions = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts")
        splitter = "/"
    if base is None or not base.is_dir():
        raise PolicyError("package/source directory missing; module coverage cannot be checked")
    names = set()
    for path in base.iterdir():
        if path.is_dir() and any(p.is_file() and p.suffix in extensions for p in path.rglob("*")):
            names.add(path.name)
        elif path.is_file() and path.suffix in extensions and path.stem not in ("__init__", "__main__"):
            if not path.name.endswith(".d.ts"):
                names.add(path.stem)
    declared = {m.split(splitter, 1)[0] for m in policy["modules"]}
    excluded = {m.split(splitter, 1)[0] for m in policy.get("unmanaged_modules", [])}
    return sorted(names - declared - excluded)


def _run(label: str, argv: list[str], root: Path, env: dict, timeout_seconds: int) -> dict:
    print(f"CHECK {label}: {' '.join(argv)}", flush=True)
    try:
        completed = subprocess.run(argv, cwd=root, env=env, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                                   timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        completed = None
        output = (exc.stdout or b"")
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        output += f"\ncommand exceeded {timeout_seconds}s timeout"
        code = 124
    except OSError as exc:
        completed = None
        output, code = str(exc), 127
    else:
        output, code = completed.stdout, completed.returncode
    if code:
        print(f"FAIL {label} (exit {code})", flush=True)
        print("\n".join(output.splitlines()[-50:]), flush=True)
    else:
        print(f"PASS {label}", flush=True)
    return {"name": label, "passed": code == 0, "exit_code": code}


def _git(root: Path, argv: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(["git", *argv], cwd=root, text=True, encoding="utf-8", errors="replace",
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PolicyError(f"git unavailable: {exc}")
    return completed.returncode, completed.stdout


def strict_policy_checks(root: Path, policy: dict, base: str) -> list[dict]:
    """Strict mode gate: every loosening needs a fresh relaxations entry; policy edits stay isolated. CI only."""
    def fail(detail: str) -> list[dict]:
        return [{"name": "strict-policy", "passed": False, "exit_code": 1, "detail": detail}]

    code, _ = _git(root, ["rev-parse", "--verify", base])
    if code:
        return fail(f"cannot resolve --ci-base ref {base!r}")
    code, old_text = _git(root, ["show", f"{base}:.agent-engineering/policy.json"])
    old_policy: object = {}
    if code:
        lsc, listing = _git(root, ["ls-tree", "--name-only", base, "--", ".agent-engineering/policy.json"])
        if lsc == 0 and listing.strip():
            return fail(f"cannot read base policy at {base}: {old_text.strip()[:200]}")
    else:
        try:
            old_policy = json.loads(old_text)
        except json.JSONDecodeError:
            return fail(f"base policy at {base} is not valid JSON")
    code, names = _git(root, ["-c", "core.quotePath=false", "diff", "--name-only", f"{base}...HEAD"])
    if code:
        return fail("git diff failed: " + names.strip()[:200])
    problems: list[str] = []
    if not isinstance(old_policy, dict):
        problems.append("base policy is not an object")
    else:
        try:
            actual = relaxation_changes(old_policy, policy)
        except (TypeError, ValueError, KeyError, IndexError, AttributeError) as exc:
            problems.append(f"cannot compare against base policy at {base}: {exc!r}")
            actual = []
        old_entries = old_policy.get("relaxations")
        old_count = len(old_entries) if isinstance(old_entries, list) else 0
        if len(policy.get("relaxations") or []) < old_count:
            problems.append("relaxations history was truncated: entries cannot be deleted")
        fresh = {change for entry in (policy.get("relaxations") or [])[old_count:]
                 for change in entry.get("changes", [])}
        unexplained = [change for change in actual if change not in fresh]
        if unexplained:
            problems.append("naked relaxation; record a relaxations entry (aegkit relax): "
                            + "; ".join(unexplained))
    changed = [line.strip() for line in names.splitlines() if line.strip()]
    if ".agent-engineering/policy.json" in changed:
        def covered(name: str) -> bool:
            return (name.startswith(".agent-engineering/")
                    or name == ".github/workflows/engineering-guard.yml"
                    or name == "ARCHITECTURE.md")
        stray = sorted({name for name in changed if not covered(name)})
        if stray:
            problems.append("policy change must be an isolated PR (allowed: .agent-engineering/**, "
                            ".github/workflows/engineering-guard.yml, ARCHITECTURE.md); offending: "
                            + ", ".join(stray))
    if problems:
        return fail(" | ".join(problems))
    return [{"name": "strict-policy", "passed": True, "exit_code": 0}]


def _report(policy: dict, results: list[dict]) -> dict:
    return {
        "schema_version": 1, "kit_version": VERSION, "policy_sha256": policy_fingerprint(policy),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": all(r["passed"] for r in results), "checks": results,
    }


def check(root: Path, policy: dict, arch_only: bool = False, ci_base: str | None = None) -> dict:
    if not policy["tests"] and not arch_only:
        raise PolicyError("behavior tests are not configured; set 'tests' or use --arch-only for diagnosis")
    absent = missing_modules(root, policy)
    if absent:
        raise PolicyError("declared modules do not exist (refusing vacuous pass): " + ", ".join(absent))
    ungoverned = ungoverned_modules(root, policy)
    if ungoverned:
        raise PolicyError("unguarded top-level modules: " + ", ".join(ungoverned) +
                          "; declare in modules or explicitly set unmanaged_modules")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(root / "src"), str(root), env.get("PYTHONPATH", "")])
    timeout_seconds = policy.get("command_timeout_seconds", 900)
    results: list[dict] = []
    if policy.get("strict"):
        if ci_base and set(ci_base) != {"0"}:
            results = strict_policy_checks(root, policy, ci_base)
            for item in results:
                print(("PASS " if item["passed"] else "FAIL ") + item["name"], flush=True)
                if not item["passed"]:
                    print(item["detail"], flush=True)
            if not all(item["passed"] for item in results):
                return _report(policy, results)
        else:
            # 全零 sha（GitHub 首推的 before 值）视同未传：无 base 可比，不误报首推。
            print("NOTE: strict policy isolation is enforced in CI (pass --ci-base there)", flush=True)
    with tempfile.TemporaryDirectory(prefix="aegkit-") as temp:
        if policy["language"] == "python":
            config = Path(temp) / ".importlinter"
            config.write_text(render_python(policy), encoding="utf-8")
            arch = ["lint-imports", "--config", str(config)]
        else:
            config = Path(temp) / ".dependency-cruiser.cjs"
            config.write_text(render_typescript(policy), encoding="utf-8")
            source = root / policy["source"]
            extensions = {".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"}
            files = sorted(p.relative_to(root).as_posix() for p in source.rglob("*")
                           if p.is_file() and p.suffix in extensions and not p.name.endswith(".d.ts"))
            if not files:
                raise PolicyError("No TypeScript/JavaScript source files found; refusing an empty architecture scan")
            arch = ["npx", "--no-install", "depcruise", "--config", str(config)]
            if (root / "tsconfig.json").is_file():
                arch += ["--ts-config", "tsconfig.json"]
            arch += files
        results.append(_run("architecture", arch, root, env, timeout_seconds))
    if not arch_only:
        for index, command in enumerate(policy["tests"], 1):
            results.append(_run(f"behavior-{index}", command, root, env, timeout_seconds))
    return _report(policy, results)


def doctor(root: Path, policy: dict) -> int:
    issues = []
    absent = missing_modules(root, policy)
    if absent:
        issues.append("missing modules: " + ", ".join(absent))
    try:
        ungoverned = ungoverned_modules(root, policy)
        if ungoverned:
            issues.append("unguarded modules: " + ", ".join(ungoverned))
    except PolicyError as exc:
        issues.append(str(exc))
    if not policy["tests"]:
        issues.append("no behavior tests configured")
    binary = "lint-imports" if policy["language"] == "python" else "npx"
    if not shutil.which(binary):
        issues.append(f"{binary} unavailable (install language adapter dependencies)")
    elif policy["language"] == "typescript":
        try:
            probe = subprocess.run(["npx", "--no-install", "depcruise", "--version"],
                                   cwd=root, capture_output=True, timeout=15)
            if probe.returncode:
                issues.append("dependency-cruiser unavailable (install as project devDependency)")
        except (OSError, subprocess.TimeoutExpired):
            issues.append("dependency-cruiser unavailable or version probe timed out")
    if not (root / ".github/workflows/engineering-guard.yml").is_file():
        issues.append("CI workflow missing")
    if policy.get("strict"):
        vendored_guard = root / ".agent-engineering/guard.py"
        if vendored_guard.is_file() and "--ci-base" not in vendored_guard.read_text(encoding="utf-8"):
            issues.append("strict policy but vendored guard.py predates strict mode (no --ci-base); run aegkit sync")
        workflow_file = root / ".github/workflows/engineering-guard.yml"
        if workflow_file.is_file() and "GUARD_CI_BASE" not in workflow_file.read_text(encoding="utf-8"):
            issues.append("strict policy but CI workflow does not pass GUARD_CI_BASE; run aegkit sync")
    for issue in issues:
        print("ISSUE:", issue)
    if not issues:
        print("PASS: policy, modules, checks and tool look ready; run check to verify behavior")
    return 1 if issues else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Engineering Kit project gate")
    parser.add_argument("command", choices=("check", "doctor", "render-config"))
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="project root (default: current directory)")
    parser.add_argument("--arch-only", action="store_true", help="diagnose architecture without behavior tests")
    parser.add_argument("--json-report", type=Path, help="write a concise JSON result only when explicitly requested")
    parser.add_argument("--ci-base", help="git base ref that enables strict policy checks (CI only)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        policy = load_policy(root)
        if args.command == "doctor":
            return doctor(root, policy)
        if args.command == "render-config":
            print(render_python(policy) if policy["language"] == "python" else render_typescript(policy))
            return 0
        result = check(root, policy, args.arch_only, args.ci_base)
        if args.json_report:
            args.json_report.parent.mkdir(parents=True, exist_ok=True)
            args.json_report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print("RESULT:", "PASS" if result["passed"] else "FAIL")
        return 0 if result["passed"] else 1
    except PolicyError as exc:
        if args.command == "check" and args.json_report:
            failure = {"schema_version": 1, "kit_version": VERSION, "passed": False,
                       "error": str(exc), "checks": []}
            args.json_report.parent.mkdir(parents=True, exist_ok=True)
            args.json_report.write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
        print("ERROR:", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
