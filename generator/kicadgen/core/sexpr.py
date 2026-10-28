"""S-expression reader/writer shared by the symbol resolver and schematic writer.

Every KiCad file (libraries and schematics) is an S-expression. Instead of
building the output with string concatenation, everything is represented as a
tree of atoms and lists and serialized in one place, with correct quoting and
escaping.

Representation:
- A node is either an atom (``str``) or a ``list`` of nodes.
- The first element of every list is its tag, e.g. ``"wire"``.
- Atoms that were quoted in the source are wrapped in :class:`Q` so the
  serializer can reproduce them faithfully.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+')
_BARE = re.compile(r"^[A-Za-z0-9_.+*/#~:\-]+$")


class Q(str):
    """An atom that was (and therefore will be) quoted, e.g. ``"UART_TX"``."""

    __slots__ = ()


def _unescape(token: str) -> str:
    body = token[1:-1]
    return re.sub(r"\\(.)", r"\1", body)


def parse(text: str) -> list:
    """Parse KiCad S-expression text into a tree of lists/atoms.

    Raises :class:`ValueError` on unbalanced input.
    """
    stack: list[list] = [[]]
    for tok in _TOKEN.findall(text):
        if tok == "(":
            new: list = []
            stack[-1].append(new)
            stack.append(new)
        elif tok == ")":
            if len(stack) == 1:
                raise ValueError("unbalanced ')' in S-expression")
            stack.pop()
        elif tok.startswith('"'):
            stack[-1].append(Q(_unescape(tok)))
        else:
            stack[-1].append(str(tok))
    if len(stack) != 1:
        raise ValueError("unbalanced '(' in S-expression")
    root = stack[0]
    # A library file is a single top-level list.
    if len(root) == 1 and isinstance(root[0], list):
        return root[0]
    return root


def dump(node, depth: int = 0) -> str:
    """Serialize a tree back into KiCad S-expression text.

    KiCad-style layout: the tag and any directly attached atoms stay on the
    opening line; nested lists are indented one tab per depth level.
    """
    if not isinstance(node, list):
        return _atom_text(node)
    indent = "\t" * depth
    tag = _tag_text(node[0]) if node and isinstance(node[0], str) else ""
    header = indent + "(" + tag
    for sub in node[1:]:
        if isinstance(sub, list):
            break
        header += " " + _atom_text(sub)
    sublists = [sub for sub in node[1:] if isinstance(sub, list)]
    if not sublists:
        return header + ")"
    body = "\n".join(dump(sub, depth + 1) for sub in sublists)
    return f"{header}\n{body}\n{indent})"


def _tag_text(node) -> str:
    """Tags are always bare atoms (KiCad tags are never quoted)."""
    s = str(node)
    if s and _BARE.match(s):
        return s
    return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')


def _atom_text(node) -> str:
    """Render one atom, quoting unless it is safe bare text or a number."""
    if isinstance(node, bool):
        return "yes" if node else "no"
    if isinstance(node, (int,)):
        return str(node)
    if isinstance(node, float):
        return ("%.10g" % node)
    s = str(node)
    if isinstance(node, Q):
        pass
    elif s != "" and _BARE.match(s):
        return s
    return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')


def find_all(node, tag: str) -> list:
    """Return every descendant list whose first atom equals ``tag``."""
    out: list = []
    if isinstance(node, list):
        if node and isinstance(node[0], str) and node[0] == tag:
            out.append(node)
        for child in node:
            out.extend(find_all(child, tag))
    return out


def first(node, tag: str):
    return next((n for n in find_all(node, tag) if n is not node), None)


def atom(node) -> str:
    """First atom of a node (its tag or key value), as plain str."""
    if isinstance(node, str):
        return str(node)
    if node and isinstance(node[0], str):
        return str(node[0])
    return ""
