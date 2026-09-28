from mdict_audio_app.domain import AudioKind, ResolutionStatus
from mdict_audio_app.services.dictionary_query import DictionaryQueryService


def _dictionary(catalog, name, priority, *, enabled=True, status="READY"):
    dictionary_id = catalog.create_dictionary(
        name=name,
        mdx_sha256=name.encode().ljust(32, b"_"),
        source_mdx_name=f"{name}.mdx",
        enabled=enabled,
        status=status,
    )
    catalog.connection.execute(
        "UPDATE dictionary SET priority = ? WHERE id = ?", (priority, dictionary_id)
    )
    return dictionary_id


def _entry(catalog, dictionary_id, word="Word", text="definition"):
    entry_id = catalog.insert_entry(
        dictionary_id=dictionary_id,
        headword=word,
        definition_html=f"<p>{text}</p>",
        definition_text=text,
        source_order=0,
    )
    example_id = catalog.connection.execute(
        "INSERT INTO example(entry_id, text, source_order) VALUES (?, ?, ?)",
        (entry_id, f"{word} example", 0),
    ).lastrowid
    return entry_id, int(example_id)


def _audio(
    catalog,
    dictionary_id,
    entry_id,
    *,
    resource_id,
    kind=AudioKind.HEADWORD,
    example_id=None,
):
    shard_id = catalog.create_audio_shard(filename=f"test-{dictionary_id}-{entry_id}.db")
    resource_id = catalog.register_audio(
        sha256=f"{dictionary_id}-{entry_id}".encode().ljust(32, b"_"),
        media_type="audio/mpeg",
        duration_ms=100,
        byte_size=1,
        shard_id=shard_id,
        blob_id=resource_id,
    )
    catalog.resolve_audio_link(
        entry_id=entry_id,
        resource_id=resource_id,
        kind=kind,
        resolution_status=ResolutionStatus.MATCHED,
        source_ref=f"{dictionary_id}.mp3",
        source_order=0,
        example_id=example_id,
    )


def test_lookup_keeps_definition_and_examples_in_highest_priority_dictionary(catalog):
    low = _dictionary(catalog, "low", 10)
    high = _dictionary(catalog, "high", 1)
    low_entry, _ = _entry(catalog, low, text="low definition")
    high_entry, _ = _entry(catalog, high, text="high definition")
    _audio(catalog, low, low_entry, resource_id=10)

    selection = DictionaryQueryService(catalog).lookup("  ｗｏｒｄ  ", True)

    assert selection.dictionary_id == high
    assert {entry.dictionary_id for entry in selection.entries} == {high}
    assert {example.dictionary_id for example in selection.examples} == {high}
    assert selection.entries[0].definition_text == "high definition"
    assert selection.headword_audio.dictionary_id == low


def test_lookup_ignores_disabled_and_non_ready_dictionaries(catalog):
    disabled = _dictionary(catalog, "disabled", 0, enabled=False)
    importing = _dictionary(catalog, "importing", 1, status="IMPORTING")
    _entry(catalog, disabled)
    _entry(catalog, importing)

    assert DictionaryQueryService(catalog).lookup("word", True) is None


def test_lookup_does_not_fallback_audio_when_disabled(catalog):
    high = _dictionary(catalog, "high", 0)
    disabled = _dictionary(catalog, "disabled", 1, enabled=False)
    high_entry, _ = _entry(catalog, high)
    disabled_entry, _ = _entry(catalog, disabled)
    _audio(catalog, disabled, disabled_entry, resource_id=22)

    selection = DictionaryQueryService(catalog).lookup("word", True)

    assert selection.dictionary_id == high
    assert selection.headword_audio is None


def test_lookup_can_disable_headword_audio_fallback(catalog):
    high = _dictionary(catalog, "high", 0)
    low = _dictionary(catalog, "low", 1)
    _entry(catalog, high)
    low_entry, _ = _entry(catalog, low)
    _audio(catalog, low, low_entry, resource_id=23)

    selection = DictionaryQueryService(catalog).lookup("word", False)

    assert selection.dictionary_id == high
    assert selection.headword_audio is None


def test_lookup_returns_word_for_matched_audio_link(catalog):
    dictionary_id = _dictionary(catalog, "audio")
    entry_id, _ = _entry(catalog, dictionary_id, word="spoken")
    _audio(catalog, dictionary_id, entry_id, resource_id=31)

    selection = DictionaryQueryService(catalog).lookup("spoken", False)

    assert selection is not None
    assert selection.headword_audio is not None
    assert selection.headword_audio.word == "spoken"


def test_lookup_does_not_leak_foreign_example_from_malformed_audio_link(catalog):
    high = _dictionary(catalog, "high", 0)
    low = _dictionary(catalog, "low", 1)
    high_entry, _ = _entry(catalog, high)
    low_entry, low_example = _entry(catalog, low)
    _audio(catalog, high, high_entry, resource_id=24, example_id=low_example)

    selection = DictionaryQueryService(catalog).lookup("word", False)

    assert selection.dictionary_id == high
    assert selection.audio_links == ()
