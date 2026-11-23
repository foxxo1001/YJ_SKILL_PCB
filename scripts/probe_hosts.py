"""Check host discovery from a disposable project, retaining a minimal transcript."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from install_skill import SKILL, install


def version(command):
    output = subprocess.run([*command, "--version"], capture_output=True, timeout=10, text=True, encoding="utf-8")
    return output.stdout.strip()


def zcode_command():
    if shutil.which("zcode"):
        return ["zcode"]
    # The desktop build ships zcode.cjs without adding it to PATH; it runs under any Node.js.
    script = os.environ.get("ZCODE_CLI")
    return ["node", script] if script and Path(script).is_file() else None


def probe_zcode(folder):
    command = zcode_command()
    if not command:
        return {"status": "not_run", "reason": "command unavailable; set ZCODE_CLI to the zcode.cjs path"}
    invocation = subprocess.run([*command, "skills", "list", "--json"], cwd=folder, capture_output=True, timeout=45,
                                text=True, encoding="utf-8", errors="replace")
    try:
        payload = json.loads(invocation.stdout)
        listed = payload.get("skills", []) if isinstance(payload, dict) else []
    except ValueError:
        listed = []
    # A user-level copy is listed in every project; only a copy inside this folder proves project discovery.
    loaded = [skill["path"] for skill in listed
              if skill.get("name") == SKILL and _inside(skill.get("rootPath"), folder)]
    found = len(loaded) == 1
    return {"status": "discovered" if found else "blocked", "exit_code": invocation.returncode,
            "version": version(command), "skill": SKILL, "discovered": found, "activation": "not_run",
            "path": loaded[0] if found else None,
            "reason": "" if found else "Skill absent from the project scope of `skills list`"}


def probe(host, folder, timeout):
    if host == "zcode":
        return probe_zcode(folder)
    name = shutil.which(host)
    if not name:
        return {"status": "not_run", "reason": "command unavailable"}
    args = ([name, "debug", "prompt-input", "Offline skills discovery check"] if host == "codex"
            else [name, "-p", "--output-format", "stream-json", "--verbose", "--max-turns", "1", "--tools", "",
                  "--setting-sources", "project", "--no-session-persistence", "Reply with OK. Do not use tools."])
    try:
        invocation = subprocess.run(args, cwd=folder, capture_output=True, timeout=timeout,
                                    text=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        content = (exc.stdout or b"").decode("utf-8", "replace")
        return {"status": "blocked", "version": version([name]), "reason": "timeout",
                "discovered_in_partial_transcript": host == "claude" and any(_claude_discovery(content))}
    if host == "codex":
        found = SKILL in invocation.stdout and "SKILL.md" in invocation.stdout
    else:
        found = any(_claude_discovery(invocation.stdout))
    return {"status": "discovered" if found else "blocked", "exit_code": invocation.returncode,
            "version": version([name]), "skill": SKILL, "discovered": found,
            "activation": "not_run", "reason": "" if found else "No verified skill metadata in host response"}


def _claude_discovery(text):
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
            yield SKILL in event.get("skills", []) or SKILL in event.get("slash_commands", [])


def _inside(path, folder):
    try:
        return Path(path).resolve().is_relative_to(folder.resolve())
    except (TypeError, OSError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", action="store_true", help="Allow a one-turn Claude Code discovery check")
    parser.add_argument("--zcode", action="store_true", help="Run the offline ZCode discovery check")
    args = parser.parse_args()
    hosts = ["codex"] + (["claude"] if args.claude else [])
    with TemporaryDirectory(prefix="ehe-skill-") as folder:
        project = Path(folder)
        install(project, "project", hosts)
        results = {host: probe(host, project, 45) for host in hosts}
        if args.zcode:
            # Keep the ZCode copy alone: ZCode would also read a Codex install under .agents/skills.
            isolated = project / "zcode"
            isolated.mkdir()
            install(isolated, "project", ["zcode"])
            results["zcode"] = probe("zcode", isolated, 45)
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
