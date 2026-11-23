"""Exercise fixed metadata calls in a disposable native editor session."""
import argparse
from pathlib import Path
import re
from time import sleep

from ehe.adapters import easyeda
from ehe.io import format_json, sha256


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bare-session", action="store_true")
    parser.add_argument("--project-copy", type=Path, help="An explicitly authorized disposable native reference project")
    args = parser.parse_args()
    source = args.project_copy.resolve() if args.project_copy else None
    if source and not source.is_file():
        raise ValueError("Native project copy not found")
    details = easyeda.detect()
    evidence = {"detected": details, "active_design_read": "not_run"}
    if (args.bare_session or source) and details["status"] == "available":
        program = Path(details["path"])
        existing = easyeda.session_list()
        if existing["status"] != "available":
            print(format_json({**evidence, "failure": "ownership inventory unavailable"}))
            return 3
        ids_before = {session.get("sessionId") for session in existing["native"]["value"]}
        request = ["open", "--headless", "true"]
        if source:
            request.extend(["--path", str(source)])
            evidence["project_sha256_before_open"] = sha256(source)
        started = easyeda.call(program, request, timeout=35)
        evidence["session_open"] = started
        session = started.get("native", {}).get("value", {}).get("sessionId", "")
        if started["status"] != "available" or not re.fullmatch(r"[a-fA-F0-9-]{20,64}", session) or session in ids_before:
            print(format_json(evidence))
            return 3
        try:
            results = []
            for attempt in range(3):
                try_result = easyeda.inspect_session(session, source, opened=started["native"]["value"])
                results.append(try_result)
                if try_result["status"] == "inspected":
                    evidence["active_design_read"] = "project_metadata_verified"
                    break
                if attempt < 2:
                    sleep(2)
            evidence["read_only_attempts"] = results
            evidence["read_only_api"] = results[-1]
        finally:
            evidence["session_close"] = easyeda.call(program, ["session", "close", "--session", session, "--destroy"])
            if source and source.is_file():
                evidence["project_sha256_after_close"] = sha256(source)
    print(format_json(evidence))
    if details["status"] != "available":
        return 3
    check = evidence.get("read_only_api", {})
    return 3 if check.get("status") == "blocked" or evidence.get("session_close", {}).get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
