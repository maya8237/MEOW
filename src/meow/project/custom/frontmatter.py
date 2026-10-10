"""A small reader for the YAML frontmatter Claude Code skill and agent files use.

Supports what those files need: `key: value` scalars (optionally quoted),
inline lists (`[a, b]`), block lists (`- a`), and `|` / `>` block scalars.
"""

from pathlib import Path

_FENCE = "---"
_QUOTES = ('"', chr(39))


def _scalar(text: str) -> str:
    text = text.strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in _QUOTES:
        return text[1:-1]
    return text


def _inline(text: str) -> object:
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        return [_scalar(item) for item in text[1:-1].split(",") if item.strip()]
    return _scalar(text)


def _indented(line: str) -> bool:
    return line[:1] in {" ", "\t"}


def _block(lines: list[str], start: int, style: str) -> tuple[str, int]:
    collected = []
    index = start
    while index < len(lines) and (not lines[index].strip() or _indented(lines[index])):
        collected.append(lines[index].strip())
        index += 1
    joiner = "\n" if style == "|" else " "
    return joiner.join(collected).strip(), index


def _list(lines: list[str], start: int) -> tuple[list[str], int]:
    items = []
    index = start
    while index < len(lines) and lines[index].strip().startswith("- "):
        items.append(_scalar(lines[index].strip()[2:]))
        index += 1
    return items, index


def _split(path: Path) -> tuple[list[str], str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise ValueError(f"{path}: must start with a '---' frontmatter block")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == _FENCE)
    except StopIteration:
        raise ValueError(f"{path}: frontmatter block is not closed by '---'") from None
    return lines[1:end], "\n".join(lines[end + 1 :]).strip()


def parse(path: Path) -> tuple[dict[str, object], str]:
    """Return (frontmatter, body) of a Markdown file; raise on malformed input."""
    header, body = _split(path)
    data: dict[str, object] = {}
    index = 0
    while index < len(header):
        line = header[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _indented(line) or ":" not in line:
            raise ValueError(f"{path}: cannot read frontmatter line {line!r}")
        key, value = (part.strip() for part in line.split(":", 1))
        if value in {"|", ">", "|-", ">-"}:
            data[key], index = _block(header, index, value[0])
        elif not value:
            data[key], index = _list(header, index)
        else:
            data[key] = _inline(value)
    return data, body
