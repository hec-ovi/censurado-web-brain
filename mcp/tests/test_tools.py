"""Each tool, driven through the real server into a real child process.

The stub CLI echoes the argv (and the contents of any file argument) back, so these assert
the exact verb an agent's call turns into: the seam where a wrong flag would silently publish
the wrong thing.
"""
from __future__ import annotations

import base64
import json

from conftest import argv_of, call, files_of


# ----- articles -----


def test_article_create_writes_the_body_to_a_file_and_passes_every_field(server):
    result = call(server, "article_create", {
        "author": "ana", "title": "El apagon", "description": "Que paso anoche",
        "body": "# El apagon\n\nUn parrafo largo.", "topics": ["energia", "milei"],
        "keywords": ["luz", "corte"], "image": "/media/abc.png", "image_alt": "una torre",
        "card_type": "image", "published_at": "2026-07-01T10:00:00Z", "subtitle": "dek",
    })
    argv = argv_of(result)
    assert argv[0] == "preview"
    pairs = dict(zip(argv[1::2], argv[2::2]))
    assert pairs["--author"] == "ana" and pairs["--title"] == "El apagon"
    assert pairs["--description"] == "Que paso anoche"
    # A list and a comma string both reach the CLI as the comma string it takes.
    assert pairs["--topics"] == "energia,milei" and pairs["--keywords"] == "luz,corte"
    assert pairs["--card-type"] == "image" and pairs["--image-alt"] == "una torre"
    assert pairs["--published-at"] == "2026-07-01T10:00:00Z"
    # The body travelled as a file, because an agent has no filesystem of its own.
    assert "--body-file" in pairs
    assert list(files_of(result).values())[0] == "# El apagon\n\nUn parrafo largo."


def test_article_create_cleans_up_the_body_file(server, runner):
    call(server, "article_create", {"author": "ana", "title": "t", "description": "d",
                                    "body": "cuerpo"})
    assert list((runner.work_dir / ".tmp").glob("*.md")) == []


def _publishing_stub(repo, existing):
    """A stub CLI that answers `archive` with a corpus and echoes everything else."""
    (repo / "cli" / "censurado.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "argv = sys.argv[1:]\n"
        f"corpus = {existing!r}\n"
        "if argv and argv[0] == 'archive':\n"
        "    print(json.dumps({'total': len(corpus), 'articles': corpus}))\n"
        "    raise SystemExit(0)\n"
        "files = {p.name: p.read_text(errors='replace') for p in map(Path, argv) if p.is_file()}\n"
        "print(json.dumps({'argv': argv, 'files': files,\n"
        "                  'work': os.environ.get('CENSURADO_WORK', '')}))\n",
        encoding="utf-8")


def test_article_create_refuses_to_republish_the_same_piece(server, stub_repo):
    # The failure this catches, seen for real: asked to change a title, an agent called the
    # tool it used last time and staged a SECOND copy of the story under a new permalink.
    _publishing_stub(stub_repo, [{"slug": "el-teatro-que-sobrevive",
                                  "title": "El teatro que sobrevive",
                                  "description": "Directores reportan caidas de ingresos del 50 "
                                                 "por ciento mientras el decreto paralizo al INT.",
                                  "published_at": "2026-07-25T10:00:00Z"}])
    result = call(server, "article_create", {
        "author": "mora", "title": "Teatro independiente bajo presion",
        "description": "Directores reportan caidas de ingresos del 50 por ciento mientras el "
                       "decreto paralizo al INT.",
        "body": "el mismo cuerpo"})
    assert result["isError"] is True
    err = result["structuredContent"]["stderr"]
    assert "article_update" in err and "el-teatro-que-sobrevive" in err
    assert "second copy" in err


def test_article_create_stages_a_genuinely_different_piece(server, stub_repo):
    _publishing_stub(stub_repo, [{"slug": "el-teatro-que-sobrevive",
                                  "title": "El teatro que sobrevive",
                                  "description": "La escena independiente resiste la crisis.",
                                  "published_at": "2026-07-25T10:00:00Z"}])
    result = call(server, "article_create", {
        "author": "mora", "title": "El subte que nunca llego",
        "description": "La obra de la linea F acumula tres anos de retraso y sobrecostos.",
        "body": "otro cuerpo"})
    assert result.get("isError") is None
    assert argv_of(result)[0] == "preview"


def test_article_create_can_be_overridden_for_a_real_second_piece(server, stub_repo):
    _publishing_stub(stub_repo, [{"slug": "cronica-uno", "title": "Cronica del ensayo abierto",
                                  "description": "Primera entrega de la cobertura del festival.",
                                  "published_at": "2026-07-25T10:00:00Z"}])
    result = call(server, "article_create", {
        "author": "mora", "title": "Cronica del ensayo abierto",
        "description": "Primera entrega de la cobertura del festival.",
        "body": "segunda entrega", "allow_similar": True})
    assert result.get("isError") is None
    assert argv_of(result)[0] == "preview"


def test_article_update_sends_only_the_named_fields(server):
    argv = argv_of(call(server, "article_update", {
        "slug": "el-apagon", "title": "Titulo nuevo",
        "metadata": {"subtitle": "dek", "card": {"type": "image", "src": "/media/a.png"}},
    }))
    assert argv[0] == "edit" and argv[1] == "el-apagon"
    assert "--set" in argv and "title=Titulo nuevo" in argv
    # The card is an object, so it goes through the JSON metadata patch, not a k=v string.
    patch = json.loads(argv[argv.index("--meta-json") + 1])
    assert patch["card"]["type"] == "image" and patch["subtitle"] == "dek"
    assert "--body-file" not in argv


def test_article_update_rejects_a_non_object_metadata(server):
    envelope = call(server, "article_update",
                    {"slug": "x", "metadata": {}})["structuredContent"]
    assert envelope["ok"] is True          # an empty object is simply nothing to merge
    result = call(server, "article_update", {"slug": "x", "metadata": "subtitle=dek"})
    assert result["isError"] is True and "must be an object" in result["content"][0]["text"]


def test_article_delete_refuses_without_confirmation(server):
    result = call(server, "article_delete", {"slug": "el-apagon", "confirm": False})
    assert result["isError"] is True
    assert "confirm=true" in result["structuredContent"]["stderr"]
    assert result["structuredContent"]["exit_code"] == 2


def test_article_delete_passes_yes_when_confirmed(server):
    assert argv_of(call(server, "article_delete", {"slug": "el-apagon", "confirm": True})) == \
        ["unpublish", "el-apagon", "--yes"]


def test_article_delete_reports_where_the_slug_is_still_referenced(server, stub_repo):
    # A removed article stays listed in the rail and in a day's plan; both drop it silently at
    # render, so the rail shrinks and the day's order shifts with nothing to explain why.
    (stub_repo / "cli" / "censurado.py").write_text(
        "import json, sys\n"
        "argv = sys.argv[1:]\n"
        "if argv[:1] == ['recomendado']:\n"
        "    print(json.dumps({'slugs': ['el-apagon', 'otra-nota']}))\n"
        "elif argv[:1] == ['portada']:\n"
        "    print(json.dumps({'portadas': [{'date': '2026-07-25', 'deleted': False,\n"
        "        'entries': [{'slug': 'el-apagon', 'role': ''},\n"
        "                    {'slug': 'otra-nota', 'role': ''}]}]}))\n"
        "else:\n"
        "    print(json.dumps({'argv': argv, 'files': {}, 'work': ''}))\n",
        encoding="utf-8")
    result = call(server, "article_delete", {"slug": "el-apagon", "confirm": True})
    err = result["structuredContent"]["stderr"]
    assert "Recomendado rail" in err and "recomendado_set" in err and "'otra-nota'" in err
    assert "plan for 2026-07-25" in err and "portada_set" in err


def test_article_delete_is_quiet_when_nothing_references_the_slug(server, stub_repo):
    (stub_repo / "cli" / "censurado.py").write_text(
        "import json, sys\n"
        "argv = sys.argv[1:]\n"
        "if argv[:1] == ['recomendado']:\n"
        "    print(json.dumps({'slugs': ['otra-nota']}))\n"
        "elif argv[:1] == ['portada']:\n"
        "    print(json.dumps({'portadas': []}))\n"
        "else:\n"
        "    print(json.dumps({'argv': argv, 'files': {}, 'work': ''}))\n",
        encoding="utf-8")
    result = call(server, "article_delete", {"slug": "el-apagon", "confirm": True})
    assert "STILL REFERENCED" not in result["structuredContent"]["stderr"]


def test_article_list_and_get_map_to_the_read_verbs(server):
    assert argv_of(call(server, "article_list", {"author": "ana", "limit": 5})) == \
        ["archive", "ana", "--limit", "5"]
    assert argv_of(call(server, "article_get", {"slug": "el-apagon"})) == ["get", "el-apagon"]


# ----- layout -----


def test_portada_set_builds_the_plan_and_keeps_the_order(server, stub_repo):
    _portada_stub(stub_repo, ["lead", "a", "b"])
    argv = argv_of(call(server, "portada_set", {"date": "2026-07-01", "entries": [
        {"slug": "lead"}, {"slug": "a", "role": ""}, {"slug": "b", "role": "important"}]}))
    assert argv[:2] == ["portada", "2026-07-01"] and argv[2] == "--set-json"
    plan = json.loads(argv[3])
    assert [e["slug"] for e in plan["entries"]] == ["lead", "a", "b"]
    assert plan["entries"][0]["role"] == "" and plan["entries"][2]["role"] == "important"


def _portada_stub(repo, day_slugs, rail=()):
    """A stub CLI that knows which articles published on a day, and what the rail holds."""
    (repo / "cli" / "censurado.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "argv = sys.argv[1:]\n"
        f"day = {list(day_slugs)!r}\n"
        f"rail = {list(rail)!r}\n"
        "if argv[:2] == ['archive', '--day']:\n"
        "    print(json.dumps({'total': len(day),\n"
        "                      'articles': [{'slug': s, 'title': s} for s in day]}))\n"
        "    raise SystemExit(0)\n"
        "if argv[:1] == ['recomendado']:\n"
        "    print(json.dumps({'slugs': rail}))\n"
        "    raise SystemExit(0)\n"
        "print(json.dumps({'argv': argv, 'files': {}, 'work': ''}))\n",
        encoding="utf-8")


def test_portada_set_refuses_slugs_that_did_not_publish_that_day(server, stub_repo):
    # A slug from another day is dropped at render with no error and everything shifts up, so
    # the intended lead silently becomes a different story.
    _portada_stub(stub_repo, ["hoy-uno", "hoy-dos"])
    result = call(server, "portada_set", {"date": "2026-07-25", "entries": [
        {"slug": "hoy-uno"}, {"slug": "de-ayer"}]})
    assert result["isError"] is True
    err = result["structuredContent"]["stderr"]
    assert "de-ayer" in err and "hoy-uno" in err
    assert "recomendado_set" in err          # the right tool for an older piece


def test_portada_set_clears_the_role_on_the_lead(server, stub_repo):
    _portada_stub(stub_repo, ["a", "b", "c"])
    result = call(server, "portada_set", {"date": "2026-07-25", "entries": [
        {"slug": "a", "role": "important"}, {"slug": "b"}, {"slug": "c"}]})
    plan = json.loads(argv_of(result)[3])
    assert plan["entries"][0]["role"] == ""
    assert "full width by position" in result["structuredContent"]["stderr"]


def test_portada_set_warns_about_a_gap_in_the_grid(server, stub_repo):
    # lead, one single, then a double: the lone single sits beside an empty cell.
    _portada_stub(stub_repo, ["a", "b", "c"])
    result = call(server, "portada_set", {"date": "2026-07-25", "entries": [
        {"slug": "a"}, {"slug": "b"}, {"slug": "c", "role": "important"}]})
    assert "LAYOUT:" in result["structuredContent"]["stderr"]
    assert "empty cell" in result["structuredContent"]["stderr"]


def test_portada_set_is_quiet_when_the_grid_comes_out_even(server, stub_repo):
    _portada_stub(stub_repo, ["a", "b", "c", "d"])
    result = call(server, "portada_set", {"date": "2026-07-25", "entries": [
        {"slug": "a"}, {"slug": "b"}, {"slug": "c"}, {"slug": "d", "role": "important"}]})
    assert "LAYOUT:" not in result["structuredContent"]["stderr"]


def test_portada_set_reminds_that_the_rail_is_separate(server, stub_repo):
    # The rail is the surface operators forget; every arrangement says where it stands.
    _portada_stub(stub_repo, ["a", "b"], rail=["viejo-uno", "viejo-dos"])
    result = call(server, "portada_set", {"date": "2026-07-25",
                                          "entries": [{"slug": "a"}, {"slug": "b"}]})
    err = result["structuredContent"]["stderr"]
    assert "RECOMENDADO" in err and "2 slug(s)" in err and "recomendado_set" in err


def test_portada_set_refuses_an_invented_role(server):
    result = call(server, "portada_set", {"date": "2026-07-01",
                                          "entries": [{"slug": "a", "role": "huge"}]})
    assert result["isError"] is True
    assert "important" in result["structuredContent"]["stderr"]


def test_portada_set_refuses_an_empty_plan(server):
    result = call(server, "portada_set", {"date": "2026-07-01", "entries": []})
    assert result["isError"] is True


def test_recomendado_set_replaces_or_clears_the_rail(server):
    assert argv_of(call(server, "recomendado_set", {"slugs": ["a", "b"]})) == \
        ["recomendado", "--set", "a,b"]
    assert argv_of(call(server, "recomendado_set", {"slugs": []})) == ["recomendado", "--clear"]


def test_recomendado_set_refuses_more_than_ten(server):
    result = call(server, "recomendado_set", {"slugs": [f"s{i}" for i in range(11)]})
    assert result["isError"] is True
    assert "at most 10" in result["structuredContent"]["stderr"]


# ----- authors -----


def test_author_create_writes_the_persona_json(server):
    result = call(server, "author_create", {
        "display_name": "Ana Rivas", "beat": "politics", "who_i_am": "Cubro el Congreso.",
        "style": "Frases cortas.", "language": "es",
        "few_shots_pos": [{"prompt": "un veto", "good": "asi lo contaria"}],
    })
    argv = argv_of(result)
    assert argv[0] == "create-author" and argv[1] == "--file"
    persona = json.loads(list(files_of(result).values())[0])
    assert persona["display_name"] == "Ana Rivas" and persona["beat"] == "politics"
    assert persona["few_shots_pos"][0]["good"] == "asi lo contaria"


def test_author_create_refuses_an_incomplete_persona(server, runner):
    # A persona with no voice notes never reaches the backend: the schema check catches it
    # first, and the handler refuses again for a client that skipped the schema.
    result = call(server, "author_create", {"display_name": "Ana", "beat": "politics",
                                            "who_i_am": "x", "style": ""})
    assert result["isError"] is True
    assert "missing required argument: style" in result["content"][0]["text"]

    from mcp_tools import h_author_create
    direct = h_author_create({"display_name": "Ana", "beat": "politics", "who_i_am": "x"}, runner)
    assert direct["ok"] is False and "synthesize" in direct["stderr"]


def test_author_update_splits_public_private_and_topics(server):
    argv = argv_of(call(server, "author_update", {
        "id": "ana", "about": "Soy Ana.", "avatar": "/media/ana.png", "beat": "economia",
        "few_shots_neg": [{"prompt": "p", "bad": "b"}], "profile_topics": ["milei", "fmi"]}))
    assert argv[:2] == ["edit-author", "ana"]
    assert "about=Soy Ana." in argv and "avatar=/media/ana.png" in argv
    assert "beat=economia" in argv
    tail = json.loads(argv[argv.index("--meta-json") + 1])
    assert tail["few_shots_neg"][0]["bad"] == "b"
    assert argv[argv.index("--profile-topics") + 1] == "milei,fmi"


def test_author_update_refuses_a_call_that_changes_nothing(server):
    result = call(server, "author_update", {"id": "ana"})
    assert result["isError"] is True
    assert "name at least one field" in result["structuredContent"]["stderr"]


def test_author_delete_is_gated_then_passes_yes(server):
    assert call(server, "author_delete", {"id": "ana", "confirm": False})["isError"] is True
    assert argv_of(call(server, "author_delete", {"id": "ana", "confirm": True})) == \
        ["remove-author", "ana", "--yes"]


def test_author_sources_read_and_replace(server):
    assert argv_of(call(server, "author_sources_get", {"id": "ana"})) == ["sources", "ana"]
    assert argv_of(call(server, "author_sources_set", {"id": "ana", "sources": ["lanacion", "p12"]})) \
        == ["sources", "ana", "--set", "lanacion,p12"]
    assert argv_of(call(server, "source_catalog", {})) == ["portals"]


# ----- media and images -----


def test_media_upload_accepts_carried_bytes(server):
    blob = base64.b64encode(b"\x89PNG\r\n\x1a\n fake").decode()
    argv = argv_of(call(server, "media_upload", {"base64_data": blob, "filename": "hero.png"}))
    assert argv[0] == "media" and argv[1].endswith("hero.png")


def test_media_upload_rejects_a_path_traversing_filename(server):
    blob = base64.b64encode(b"x").decode()
    result = call(server, "media_upload", {"base64_data": blob, "filename": "../../etc/passwd"})
    assert result["isError"] is True
    assert "plain name" in result["structuredContent"]["stderr"]


def test_media_upload_needs_exactly_one_source(server):
    assert call(server, "media_upload", {})["isError"] is True
    both = call(server, "media_upload", {"path": "/tmp/a.png", "base64_data": "eA=="})
    assert both["isError"] is True


def test_image_generate_passes_the_brief_and_the_geometry(server):
    argv = argv_of(call(server, "image_generate", {
        "prompt": "una torre de alta tension al amanecer", "alt": "torre", "width": 1344,
        "height": 768, "steps": 4}))
    assert argv[0] == "image"
    pairs = dict(zip(argv[1::2], argv[2::2]))
    assert pairs["--prompt"].startswith("una torre") and pairs["--width"] == "1344"


# ----- the recipe and the walk -----


def test_recipe_reads_and_writes(server):
    assert argv_of(call(server, "editorial_style", {})) == ["style"]
    assert argv_of(call(server, "editorial_rules", {"lang": "en"})) == ["editorial-rules", "en"]
    assert argv_of(call(server, "editorial_rules", {})) == ["editorial-rules", "es"]
    assert argv_of(call(server, "prompt_get", {"key": "persona/synthesize.md"})) == \
        ["prompt", "persona/synthesize.md"]
    written = call(server, "prompt_set", {"key": "workflow/50-draft.md", "body": "nuevo cuerpo"})
    assert argv_of(written)[:2] == ["set-prompt", "workflow/50-draft.md"]
    assert list(files_of(written).values())[0] == "nuevo cuerpo"


def test_workflow_step_serves_one_node(server):
    assert argv_of(call(server, "workflow_step", {"mode": "single-article"})) == \
        ["step", "--mode", "single-article"]
    assert argv_of(call(server, "workflow_step", {"mode": "daily", "node": "30-research"})) == \
        ["step", "30-research", "--mode", "daily"]
    assert argv_of(call(server, "workflow_step", {"list": True})) == ["step", "--list"]


def test_workflow_text_is_translated_into_tool_calls(runner):
    """The walk's nodes and gate messages are written for a terminal operator. An agent here
    has no shell and no filesystem, so verbatim text sends it hunting for tools it does not
    have. This is the exact BLOCKED message the artifact gate emits."""
    from mcp_tools import tool_speak
    work = runner.work_dir
    blocked = (f"BLOCKED: you cannot start 40-outline yet. The previous step 30-research must "
               f"first do its work and save it to {work}/ledger.md. Go back: run `python3 "
               f"cli/censurado.py step 30-research --mode single-article`, complete it, write "
               f"{work}/ledger.md, then advance to 40-outline.")
    out = tool_speak(blocked, work)
    assert 'the workflow_step tool (node="30-research", mode="single-article")' in out
    assert 'workflow_save(artifact="ledger.md", content=...)' in out
    assert "censurado.py" not in out and str(work) not in out


def test_workflow_text_maps_every_verb_a_node_names(runner):
    from mcp_tools import tool_speak
    work = runner.work_dir
    node = ('List what this author published with `python3 cli/censurado.py archive <author-id> '
            '--q "<theme>"`, read the voice with `censurado.py persona <id>`, render art with '
            '`python3 cli/censurado.py image --prompt "..."`, then publish with '
            '`python3 cli/censurado.py preview --author <id>`.')
    out = tool_speak(node, work)
    assert "the article_list tool" in out and "the author_get tool" in out
    assert "the image_generate tool" in out and "the article_create tool" in out
    assert "censurado.py" not in out


def test_workflow_text_says_a_maintenance_sweep_is_not_reachable(runner):
    from mcp_tools import tool_speak
    out = tool_speak("run `censurado-brain topics cleanse --map-file map.json` to apply it",
                     runner.work_dir)
    assert "censurado-brain" not in out and "ask the human" in out


def test_workflow_text_rewrites_the_scratch_dir_line(runner):
    from mcp_tools import tool_speak
    out = tool_speak(f"WORK DIR: {runner.work_dir}  (your scratch for this piece; save the file "
                     f"each ARTIFACT line names here, nothing else on disk is yours to touch)\n"
                     f"body text stays", runner.work_dir)
    assert "workflow_save" in out and "no filesystem access" in out
    assert "body text stays" in out


def test_workflow_step_translates_what_it_serves(server, monkeypatch, runner):
    """End to end through the tool: the stub prints a node body full of CLI commands."""
    node = (f"WORK DIR: {runner.work_dir}\\nrun `python3 cli/censurado.py step 40-outline "
            f"--mode daily`\\nARTIFACT: save this step's output to {runner.work_dir}/draft.md")
    stub = (runner.repo / "cli" / "censurado.py")
    stub.write_text("import sys\nprint(\"\"\"%s\"\"\")\n" % node, encoding="utf-8")
    result = call(server, "workflow_step", {"mode": "daily"})
    text = result["structuredContent"]["stdout"]
    assert 'the workflow_step tool (node="40-outline", mode="daily")' in text
    assert 'workflow_save(artifact="draft.md", content=...)' in text


def test_workflow_save_lands_the_artifact_in_the_gate_dir(server, runner):
    result = call(server, "workflow_save", {"artifact": "draft.md", "content": "# borrador\n"})
    assert result["structuredContent"]["ok"] is True
    saved = runner.work_dir / "draft.md"
    assert saved.read_text(encoding="utf-8") == "# borrador\n"
    assert json.loads(result["structuredContent"]["stdout"])["saved"] == str(saved)


def test_workflow_save_cannot_escape_the_scratch_dir(server, runner):
    result = call(server, "workflow_save", {"artifact": "../escape.md", "content": "x"})
    assert result["isError"] is True
    assert not (runner.work_dir.parent / "escape.md").exists()


# ----- lifecycle -----


def test_stack_up_picks_the_lane(server):
    assert argv_of(call(server, "stack_up", {})) == ["up"]
    assert argv_of(call(server, "stack_up", {"gpu": True})) == ["up-gpu"]


def test_stack_status_asks_for_the_machine_readable_verdict(server):
    assert argv_of(call(server, "stack_status", {"local_only": True})) == \
        ["status", "--json", "--local-only"]


def test_site_publish_refuses_without_an_explicit_yes(server):
    result = call(server, "site_publish", {"confirm": False})
    assert result["isError"] is True
    assert "PUBLIC" in result["structuredContent"]["stderr"]


def test_site_publish_goes_live_when_confirmed(server):
    assert argv_of(call(server, "site_publish", {"confirm": True})) == ["publicar", "--yes"]


def test_sections_and_topics_read_verbs(server):
    assert argv_of(call(server, "sections_list", {"axis": "authors"})) == ["sections", "--authors"]
    assert argv_of(call(server, "topics_inventory", {"limit": 50})) == ["topics", "--limit", "50"]
    assert call(server, "topic_remove", {"slug": "ia", "confirm": False})["isError"] is True
    assert argv_of(call(server, "topic_remove", {"slug": "ia", "confirm": True})) == \
        ["remove-topic", "ia", "--yes"]
