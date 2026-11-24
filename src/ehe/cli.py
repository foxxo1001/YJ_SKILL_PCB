"""Command line interface and exit-code mapping."""
import argparse
import csv
import sqlite3
import sys
from pathlib import Path

import yaml

from . import __version__, advanced, reality
from .adapters import altium, easyeda, easyeda_circuit, easyeda_design, easyeda_review, easyeda_source, generic, kicad
from .evalops.suite import run_suite
from .knowledge import admit, claims, holdout_registry, search
from .doctor import doctor
from .domain import run_domain
from .io import format_json, load
from .project import engineering_path, init_project
from .requirements import analyze, trace
from .review import backend_for, full_review
from .report import render
from .runtime import easyeda_changes, engine
from .runtime.plan import impact

EXIT_CODES = {"fail": 1, "blocked": 3, "not_run": 3, "inconclusive": 3,
              "FAILED": 1, "REVISE": 1, "BLOCKED": 3, "PAUSED": 3, "WAITING_APPROVAL": 3,
              "BUDGET_EXHAUSTED": 3, "CANCELLED": 3}


def parser_for_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ehe")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Probe installed tools")
    init = commands.add_parser("init", help="Create a project from starter assets")
    init.add_argument("project", type=Path)
    req = commands.add_parser("analyze", help="Extract source-indexed requirement candidates")
    req.add_argument("input", type=Path)
    req.add_argument("--ocr-lang")
    req.add_argument("--mapping", type=Path)
    req.add_argument("--output", type=Path)
    budget = commands.add_parser("budget", help="Evaluate declared engineering budgets")
    budget.add_argument("project", type=Path)
    budget.add_argument("--scope", choices=("core", "reality", "advanced", "all"), default="core")
    review = commands.add_parser("review", help="Review native design files or structured exports")
    review.add_argument("domain", choices=("full", "schematic", "pcb", "export", "geometry"))
    review.add_argument("input", type=Path)
    review.add_argument("--producer", choices=("generic", "kicad", "easyeda", "altium"), default="generic")
    review.add_argument("--model", type=Path, help="Read a separate engineering model for a full review")
    design = commands.add_parser("design", help="Generate or verify a native design and report handoff evidence")
    design.add_argument("action", choices=("verify", "generate"))
    design.add_argument("project", type=Path)
    design.add_argument("--model", type=Path)
    design.add_argument("--output", type=Path, help="New directory for a generated EasyEDA project")
    for command in (review, design):
        command.add_argument("--eda", choices=("auto", "kicad", "easyeda"), default="auto")
        command.add_argument("--pcb", help="PCB title or UUID inside an EasyEDA Pro project")
        command.add_argument("--offline", action="store_true", help="Read EasyEDA source without opening an editor")
    loop = commands.add_parser("loop", help="Run or resume a persisted engineering plan")
    actions = loop.add_subparsers(dest="action", required=True)
    for action in ("run", "status", "resume", "decide", "cancel"):
        command = actions.add_parser(action)
        command.add_argument("--project", required=True, type=Path)
        if action == "run":
            command.add_argument("--goal", required=True, type=Path)
        if action == "decide":
            command.add_argument("proposal")
            command.add_argument("decision", choices=("approve", "deny"))
            command.add_argument("--reviewer", required=True)
        if action in {"decide", "cancel"}:
            command.add_argument("--reason", required=True)
    change = commands.add_parser("change", help="Propose and apply a scoped EasyEDA PCB revision")
    commands_change = change.add_subparsers(dest="action", required=True)
    for action in ("propose", "decide", "apply", "rollback"):
        command = commands_change.add_parser(action)
        command.add_argument("--project", required=True, type=Path)
        if action == "propose":
            command.add_argument("--pcb")
            candidate = command.add_mutually_exclusive_group(required=True)
            candidate.add_argument("--replacement", type=Path)
            candidate.add_argument("--edits", type=Path)
        else:
            command.add_argument("proposal")
        if action == "decide":
            command.add_argument("decision", choices=("approve", "deny"))
            command.add_argument("--reviewer", required=True)
        if action in {"decide", "rollback"}:
            command.add_argument("--reason", required=True)
    report = commands.add_parser("report", help="Render the most recent Loop report")
    report.add_argument("--project", required=True, type=Path)
    report.add_argument("--output", type=Path)
    changes = commands.add_parser("impact", help="Expand design changes into regression areas")
    changes.add_argument("labels", nargs="+")
    knowledge = commands.add_parser("knowledge", help="Import and retrieve traceable local cases")
    kb = knowledge.add_subparsers(dest="action", required=True)
    for action in ("import", "search", "claims"):
        command = kb.add_parser(action)
        command.add_argument("--db", type=Path, required=True)
        if action == "import":
            command.add_argument("input", type=Path)
            command.add_argument("--metadata", required=True, type=Path)
            command.add_argument("--registry", type=Path)
            command.add_argument("--ocr-lang")
        if action == "search":
            command.add_argument("query")
            command.add_argument("--year", type=int)
            command.add_argument("--track")
            command.add_argument("--kind")
            command.add_argument("--limit", type=int, default=12)
        if action == "claims":
            command.add_argument("entity")
            command.add_argument("parameter")
    eval_parser = commands.add_parser("eval", help="Run independent gold tests or isolate a holdout")
    eval_commands = eval_parser.add_subparsers(dest="action", required=True)
    evaluation = eval_commands.add_parser("run")
    evaluation.add_argument("--suite", choices=("core",), default="core")
    evaluation.add_argument("--suite-file", type=Path)
    evaluation.add_argument("--baseline", type=Path)
    evaluation.add_argument("--output", type=Path)
    evaluation.add_argument("--repeat", type=int, default=1)
    isolation = eval_commands.add_parser("isolate")
    isolation.add_argument("manifest", type=Path)
    isolation.add_argument("--knowledge-db", type=Path)
    isolation.add_argument("--output", type=Path, required=True)
    eda = commands.add_parser("eda", help="Probe or inspect optional native EDA surfaces")
    eda_backends = eda.add_subparsers(dest="backend", required=True)
    easy = eda_backends.add_parser("easyeda")
    easy.add_argument("mode", choices=("detect", "sessions", "inspect", "index"))
    easy.add_argument("--session")
    easy.add_argument("--expected-project", type=Path)
    easy.add_argument("--pcb", help="Inspect a specified PCB title or UUID instead of an arbitrary board")
    ad = eda_backends.add_parser("altium")
    ad.add_argument("--input", type=Path)
    return parser


def dispatch(args: argparse.Namespace) -> dict:
    if args.command == "doctor":
        return doctor()
    if args.command == "init":
        return init_project(args.project)
    if args.command == "analyze":
        report = analyze(args.input.resolve(), args.ocr_lang)
        report["trace"] = trace(report["requirements"], load(args.mapping) if args.mapping else {})
        if args.output:
            with args.output.open("x", encoding="utf-8") as target:
                target.write(format_json(report))
        return report
    if args.command == "budget":
        source = engineering_path(args.project.resolve())
        if args.scope == "core":
            return run_domain(source)
        if args.scope == "all":
            from .domain import CHECKS

            checks = {**CHECKS, **reality.CHECKS, **advanced.CHECKS}
        else:
            checks = {"reality": reality.CHECKS, "advanced": advanced.CHECKS}[args.scope]
        return run_domain(source, checks)
    if args.command == "design":
        if args.action == "generate":
            if not args.output or args.eda == "kicad" or args.model or args.pcb or args.offline:
                raise ValueError("Use design generate <spec> --output <new-directory> for an EasyEDA project")
            spec = load(args.project)
            if isinstance(spec, dict) and spec.get("format") == easyeda_circuit.FORMAT:
                return easyeda_circuit.generate(args.project, args.output)
            return easyeda_design.generate(args.project, args.output)
        if args.output:
            raise ValueError("--output applies to design generate")
        return full_review(args.project, args.model, design_verification=True, backend=args.eda,
                           pcb=args.pcb, offline=args.offline)
    if args.command == "review":
        path = args.input.resolve()
        if args.domain == "full":
            return full_review(path, args.model, backend=args.eda, pcb=args.pcb, offline=args.offline)
        if args.model or args.offline:
            raise ValueError("--model and --offline are available with review full")
        if args.domain == "export":
            return generic.ingest(path, args.producer)
        backend = backend_for(path, args.eda)
        if backend == "easyeda":
            if args.domain != "geometry":
                raise ValueError("Use review full for native EasyEDA Pro checks")
            return easyeda_review.project_geometry(easyeda_source.index_at(path), args.pcb)
        if args.pcb:
            raise ValueError("--pcb is available for EasyEDA reviews")
        if args.domain == "geometry":
            native = kicad.geometry(path)
            return {"status": "parsed", "level": "E3", "canonical": native}
        return kicad.run(path, args.domain)
    if args.command == "loop":
        if args.action == "run":
            return engine.start(args.project, args.goal)
        if args.action == "decide":
            return engine.decide(args.project, args.proposal, args.decision, args.reviewer, args.reason)
        if args.action == "cancel":
            return engine.cancel(args.project, args.reason)
        return {"status": engine.status, "resume": engine.resume}[args.action](args.project)
    if args.command == "report":
        content = render(args.project)
        if args.output:
            with args.output.open("x", encoding="utf-8") as target:
                target.write(content)
            return {"status": "written", "path": str(args.output.resolve())}
        return content
    if args.command == "impact":
        return {"status": "calculated", "impacted": impact(args.labels)}
    if args.command == "knowledge":
        if args.action == "import":
            return admit(args.db.resolve(), args.input.resolve(), load(args.metadata), args.registry, args.ocr_lang)
        if args.action == "search":
            return search(args.db, args.query, year=args.year, track=args.track, kind=args.kind, limit=args.limit)
        return claims(args.db, args.entity, args.parameter)
    if args.command == "eval":
        if args.action == "isolate":
            result = holdout_registry(args.manifest.resolve(), args.knowledge_db)
        else:
            result = run_suite(args.suite_file, load(args.baseline) if args.baseline else None, args.repeat)
        if args.output:
            with args.output.open("x", encoding="utf-8") as target:
                target.write(format_json(result))
        return result
    if args.command == "change":
        if args.action == "propose":
            if args.edits:
                return easyeda_changes.propose_edits(args.project, args.pcb, args.edits)
            return easyeda_changes.propose(args.project, args.pcb, args.replacement)
        if args.action == "decide":
            return easyeda_changes.decide(args.project, args.proposal, args.decision, args.reviewer, args.reason)
        if args.action == "rollback":
            return easyeda_changes.revise(args.project, args.proposal, rollback=args.reason)
        return easyeda_changes.revise(args.project, args.proposal)
    if args.command == "eda":
        if args.backend == "easyeda":
            if args.mode == "index":
                if not args.expected_project:
                    raise ValueError("EasyEDA index requires --expected-project")
                return easyeda.project_inventory(args.expected_project.resolve())
            if args.mode == "inspect":
                if not args.session:
                    raise ValueError("EasyEDA inspect requires --session")
                return easyeda.inspect_session(args.session, args.expected_project, pcb=args.pcb)
            return easyeda.detect() if args.mode == "detect" else easyeda.session_list()
        if not args.input:
            return altium.detect()
        if args.input.suffix.lower() == ".prjpcb":
            return altium.project_inventory(args.input.resolve())
        return altium.exported(args.input.resolve())
    raise ValueError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = parser_for_cli().parse_args(argv)
    try:
        result = dispatch(args)
        payload = result if isinstance(result, str) else format_json(result)
    except (OSError, TypeError, OverflowError, ValueError, KeyError, csv.Error, sqlite3.Error, yaml.YAMLError) as exc:
        print(format_json({"status": "invalid_input", "error": str(exc)}), file=sys.stderr)
        return 2
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(payload)
    return EXIT_CODES.get(result.get("status", result.get("state")), 0) if isinstance(result, dict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
