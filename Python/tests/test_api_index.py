from pathlib import Path

import pytest

from unreal_mcp.api_index import ApiIndex, build_index, main

STUB = Path(__file__).parent / "fixtures" / "unreal_stub_sample.py"


@pytest.fixture
def index(tmp_path):
    out = tmp_path / "api.sqlite"
    n = build_index(STUB, out)
    assert n >= 12
    return ApiIndex(out)


def test_kinds_parsed(index):
    kinds = {r["kind"] for r in index.search("actor", limit=50)}
    assert {"class", "method", "editor_property", "property"} <= kinds


def test_natural_language_finds_snake_case(index):
    top = index.search("spawn actor from class", limit=3)
    assert top[0]["qualname"] == "EditorActorSubsystem.spawn_actor_from_class"
    assert "location: Vector" in top[0]["signature"]


def test_camel_case_class_search(index):
    assert index.search("material editing", limit=1)[0]["qualname"] == "MaterialEditingLibrary"


def test_kind_filter(index):
    res = index.search("tags", kind="editor_property")
    assert [r["qualname"] for r in res] == ["Actor.tags"]


def test_doc_class_has_members_and_module(index):
    d = index.doc("actor")  # case-insensitive
    assert d["module"] == "Engine" and d["bases"] == "Object"
    names = {m["name"] for m in d["members"]}
    assert {"get_actor_location", "root_component", "tags"} <= names
    assert "Editor Properties" not in d["doc"]


def test_setter_not_duplicated(index):
    rows = index.search("root component", limit=10)
    assert len([r for r in rows if r["qualname"] == "Actor.root_component"]) == 1


def test_missing_index_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="unreal_api_build_index"):
        ApiIndex(tmp_path / "nope.sqlite")


def test_empty_query(index):
    assert index.search("!!!") == []


def test_cli(tmp_path, capsys):
    out = tmp_path / "cli.sqlite"
    assert main(["build", str(STUB), "--out", str(out)]) == 0
    assert main(["search", "get all level actors", "--index", str(out), "--limit", "1"]) == 0
    assert "get_all_level_actors" in capsys.readouterr().out


def test_api_tools(tools, conn):
    assert tools["unreal_api_search"]("actor")["success"] is False  # no index yet
    r = tools["unreal_api_build_index"](stub_path=str(STUB))
    assert r["success"] and r["symbols"] >= 12
    assert tools["unreal_api_search"]("spawn actor")["results"][0]["name"] == "spawn_actor_from_class"
    assert tools["unreal_api_doc"]("EditorActorSubsystem")["kind"] == "class"
    assert tools["unreal_api_doc"]("NoSuchThing")["success"] is False


def test_build_index_asks_editor_for_stub(fake, tools):
    fake.on("project_context", lambda p: {"python_stub_path": str(STUB)})
    assert tools["unreal_api_build_index"]()["success"]


def test_stub_with_utf8_bom(tmp_path):
    # The real UE 5.8 PythonStub/unreal.py starts with a BOM.
    from unreal_mcp.api_index import ApiIndex, build_index
    stub = tmp_path / "unreal.py"
    stub.write_bytes(b"\xef\xbb\xbf" + STUB.read_bytes())
    build_index(stub, tmp_path / "bom.sqlite")
    assert ApiIndex(tmp_path / "bom.sqlite").search("spawn actor", limit=3)
