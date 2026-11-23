"""Check host discovery from a disposable project, retaining a minimal transcript."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from install_skill import SKILL, install


def version(host):
    output = subprocess.run([host, "--version"], capture_output=True, timeout=10, text=True, encoding="utf-8")
    return output.stdout.strip()


def probe(host, folder, timeout):
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
        return {"status": "blocked", "version": version(name), "reason": "timeout",
                "discovered_in_partial_transcript": host == "claude" and any(_claude_discovery(content))}
    if host == "codex":
        found = SKILL in invocation.stdout and "SKILL.md" in invocation.stdout
    else:
        found = any(_claude_discovery(invocation.stdout))
    return {"status": "discovered" if found else "blocked", "exit_code": invocation.returncode,
            "version": version(name), "skill": SKILL, "discovered": found,
            "activation": "not_run", "reason": "" if found else "No verified skill metadata in host response"}


def _claude_discovery(text):
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
            yield SKILL in event.get("skills", []) or SKILL in event.get("slash_commands", [])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", action="store_true", help="Allow a one-turn Claude Code discovery check")
    args = parser.parse_args()
    hosts = ["codex", "claude"] if args.claude else ["codex"]
    with TemporaryDirectory(prefix="ehe-skill-") as folder:
        project = Path(folder)
        install(project, "project", hosts)
        print(json.dumps({host: probe(host, project, 45) for host in hosts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
