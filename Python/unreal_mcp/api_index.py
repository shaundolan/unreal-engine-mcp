"""Offline Unreal Python API index.

The editor writes a full stub of the ``unreal`` module to
``<Project>/Intermediate/PythonStub/unreal.py`` when Python Developer Mode is enabled
(Editor Preferences > Plugins > Python > Developer Mode). This module parses that stub
into a SQLite FTS5 index so agents can search classes, functions, methods and editor
properties locally, with no network and no cost.

CLI:
    python -m unreal_mcp.api_index build <path/to/unreal.py> [--out index.sqlite]
    python -m unreal_mcp.api_index search "spawn actor from class"
"""

from __future__ import annotations

import argparse
import ast
import re
import sqlite3
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

DOC_LIMIT = 2000
_EDITOR_PROP = re.compile(r"^\s*-\s*``(?P<name>\w+)``\s*\((?P<type>[^)]*)\):\s*(?P<desc>.*)$")
_MODULE = re.compile(r"\*\*Module\*\*:\s*(\w+)")

SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,        -- class | function | method | property | editor_property
    name TEXT NOT NULL,
    qualname TEXT NOT NULL,
    owner TEXT,
    bases TEXT,
    signature TEXT,
    module TEXT,
    doc TEXT
);
CREATE INDEX IF NOT EXISTS symbols_qualname ON symbols(qualname);
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    qualname, name_words, doc, content='', tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


@dataclass
class Symbol:
    kind: str
    name: str
    qualname: str
    owner: str | None = None
    bases: str | None = None
    signature: str | None = None
    module: str | None = None
    doc: str | None = None


def _split_words(name: str) -> str:
    """snake_case and CamelCase -> space separated words, so 'spawn actor' matches spawn_actor_from_class."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ")
    return s.lower()


def _signature(fn: ast.FunctionDef) -> str:
    try:
        args = ast.unparse(fn.args)
    except Exception:
        args = "..."
    ret = f" -> {ast.unparse(fn.returns)}" if fn.returns is not None else ""
    return f"{fn.name}({args}){ret}"


def _trim(doc: str | None) -> str | None:
    if not doc:
        return None
    doc = doc.strip()
    return doc if len(doc) <= DOC_LIMIT else doc[:DOC_LIMIT] + " ..."


def parse_stub(source: str) -> Iterator[Symbol]:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            yield Symbol("function", node.name, node.name, signature=_signature(node),
                         doc=_trim(ast.get_docstring(node)))
        elif isinstance(node, ast.ClassDef):
            yield from _parse_class(node)


def _parse_class(cls: ast.ClassDef) -> Iterator[Symbol]:
    doc = ast.get_docstring(cls) or ""
    module_match = _MODULE.search(doc)
    module = module_match.group(1) if module_match else None
    bases = ", ".join(ast.unparse(b) for b in cls.bases) or None
    # Class doc without the long editor-properties list (those become their own rows).
    head = doc.split("**Editor Properties:**")[0]
    yield Symbol("class", cls.name, cls.name, bases=bases, module=module, doc=_trim(head))
    if "**Editor Properties:**" in doc:
        for line in doc.split("**Editor Properties:**", 1)[1].splitlines():
            m = _EDITOR_PROP.match(line)
            if m:
                yield Symbol("editor_property", m["name"], f"{cls.name}.{m['name']}", owner=cls.name,
                             signature=f"{m['name']}: {m['type'].strip()}", module=module,
                             doc=_trim(m["desc"]))
    for item in cls.body:
        if not isinstance(item, ast.FunctionDef) or item.name.startswith("__"):
            continue
        is_prop = any(isinstance(d, ast.Name) and d.id == "property" for d in item.decorator_list)
        if any(isinstance(d, ast.Attribute) and d.attr == "setter" for d in item.decorator_list):
            continue
        yield Symbol("property" if is_prop else "method", item.name, f"{cls.name}.{item.name}",
                     owner=cls.name, signature=_signature(item), module=module,
                     doc=_trim(ast.get_docstring(item)))


def build_index(stub_path: Path, out_path: Path) -> int:
    source = Path(stub_path).read_text(encoding="utf-8-sig", errors="replace")  # UE writes a UTF-8 BOM
    symbols = list(parse_stub(source))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        _insert(con, symbols)
        con.execute("INSERT OR REPLACE INTO meta VALUES ('source', ?)", (str(stub_path),))
        con.execute("INSERT OR REPLACE INTO meta VALUES ('count', ?)", (str(len(symbols)),))
        con.commit()
    finally:
        con.close()
    tmp.replace(out_path)
    return len(symbols)


def _insert(con: sqlite3.Connection, symbols: Iterable[Symbol]) -> None:
    for s in symbols:
        cur = con.execute(
            "INSERT INTO symbols(kind,name,qualname,owner,bases,signature,module,doc) VALUES (?,?,?,?,?,?,?,?)",
            (s.kind, s.name, s.qualname, s.owner, s.bases, s.signature, s.module, s.doc))
        con.execute("INSERT INTO symbols_fts(rowid, qualname, name_words, doc) VALUES (?,?,?,?)",
                    (cur.lastrowid, s.qualname, _split_words(s.qualname), s.doc or ""))


def _fts_query(text: str) -> str:
    words = re.findall(r"\w+", _split_words(text))
    return " ".join(f'"{w}"' for w in words)


_COLUMNS = "kind, name, qualname, owner, bases, signature, module, doc"


def _row(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in r.keys() if r[k] is not None}


class ApiIndex:
    def __init__(self, path: Path):
        if not Path(path).exists():
            raise FileNotFoundError(
                f"no API index at {path}. Build it with unreal_api_build_index (or "
                f"`python -m unreal_mcp.api_index build <Project>/Intermediate/PythonStub/unreal.py`).")
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.con.row_factory = sqlite3.Row

    def search(self, query: str, limit: int = 20, kind: str | None = None) -> list[dict]:
        q = _fts_query(query)
        if not q:
            return []
        cols = ", ".join(f"symbols.{c.strip()}" for c in _COLUMNS.split(","))
        sql = (f"SELECT {cols} FROM symbols_fts JOIN symbols ON symbols.id = symbols_fts.rowid "
               "WHERE symbols_fts MATCH ? " + ("AND kind = ? " if kind else "") +
               "ORDER BY bm25(symbols_fts, 10.0, 5.0, 1.0) LIMIT ?")
        args = [q] + ([kind] if kind else []) + [limit]
        rows = [_row(r) for r in self.con.execute(sql, args)]
        for r in rows:
            if r.get("doc"):
                r["doc"] = r["doc"][:300]
        return rows

    def doc(self, qualname: str, member_limit: int = 200) -> dict | None:
        r = self.con.execute(f"SELECT {_COLUMNS} FROM symbols WHERE qualname = ? COLLATE NOCASE "
                             "ORDER BY kind = 'class' DESC LIMIT 1", (qualname,)).fetchone()
        if r is None:
            return None
        out = _row(r)
        if out["kind"] == "class":
            members = self.con.execute(
                "SELECT kind, name, signature FROM symbols WHERE owner = ? ORDER BY kind, name LIMIT ?",
                (out["name"], member_limit)).fetchall()
            out["members"] = [_row(m) for m in members]
        return out

    def count(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]


def main(argv: list[str] | None = None) -> int:
    from .config import Settings
    settings = Settings.from_env()
    p = argparse.ArgumentParser(prog="python -m unreal_mcp.api_index")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("stub")
    b.add_argument("--out", default=str(settings.api_index_path))
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--index", default=str(settings.api_index_path))
    s.add_argument("--limit", type=int, default=10)
    a = p.parse_args(argv)
    if a.cmd == "build":
        n = build_index(Path(a.stub), Path(a.out))
        print(f"indexed {n} symbols -> {a.out}")
    else:
        for r in ApiIndex(Path(a.index)).search(a.query, a.limit):
            print(f"{r['kind']:16} {r.get('signature') or r['qualname']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
