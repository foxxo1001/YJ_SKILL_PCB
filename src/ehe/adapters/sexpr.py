"""Restricted S-expression reader for native KiCad files."""
import re
from pathlib import Path

TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()"]+')
ESCAPE = re.compile(r'\\(["\\nrt])')


def parse(text: str) -> list:
    stack = [[]]
    end = 0
    for match in TOKEN.finditer(text):
        if text[end:match.start()].strip():
            raise ValueError("Malformed S-expression token")
        end = match.end()
        token = match[0]
        if token == "(":
            if len(stack) > 128:
                raise ValueError("S-expression nesting exceeded")
            child = []
            stack[-1].append(child)
            stack.append(child)
        elif token == ")":
            if len(stack) == 1:
                raise ValueError("Unexpected closing parenthesis")
            stack.pop()
        else:
            if token.startswith('"'):
                escapes = {'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t"}
                token = ESCAPE.sub(lambda found: escapes[found[1]], token[1:-1])
            stack[-1].append(token)
    if text[end:].strip() or len(stack) != 1 or len(stack[0]) != 1 or not isinstance(stack[0][0], list):
        raise ValueError("Incomplete S-expression")
    return stack[0][0]


def read(path: Path) -> list:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError(f"Native source exceeds 64 MiB: {path.name}")
    return parse(path.read_text(encoding="utf-8-sig"))


def all_nodes(node: list, tag: str):
    return (item for item in node if isinstance(item, list) and item and item[0] == tag)


def fields(node: list, tag: str) -> list:
    return next(all_nodes(node, tag), [tag])[1:]


def value(node: list, tag: str, default=None):
    items = fields(node, tag)
    return items[0] if items else default
