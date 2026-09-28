from mdict_audio_app.domain.models import AudioKind
from mdict_audio_app.parsing.base import AdapterRegistry
from mdict_audio_app.parsing.css_selector import CssSelectorAdapter
from mdict_audio_app.parsing.generic import GenericParser


def _adapter(name="demo"):
    return CssSelectorAdapter(
        name=name,
        title_pattern=r"Demo Dictionary",
        definition_selector=".definition",
        example_selector=".example",
        headword_audio_selector=".headword-audio",
        example_audio_selector=".example-audio",
    )


def test_css_adapter_extracts_examples_and_classifies_audio():
    parsed = _adapter().parse(
        "hello",
        "<div class='definition'>Meaning <span class='headword-audio' "
        "data-src='sound://hello.mp3'></span>"
        "<p class='example'>Example text <a class='example-audio' "
        "href='sound://example.mp3'></a></p></div>",
    )

    assert parsed.definition_text.startswith("Meaning")
    assert parsed.examples == ("Example text",)
    assert [(ref.source_ref, ref.kind, ref.example_index) for ref in parsed.audio_refs] == [
        ("hello.mp3", AudioKind.HEADWORD, None),
        ("example.mp3", AudioKind.EXAMPLE, 0),
    ]


def test_css_adapter_uses_identity_for_identical_example_nodes():
    parsed = _adapter().parse(
        "hello",
        "<div class='definition'>"
        "<p class='example'>Same <a class='example-audio' href='sound://one.mp3'></a></p>"
        "<p class='example'>Same <a class='example-audio' href='sound://two.mp3'></a></p>"
        "</div>",
    )

    assert [(ref.source_ref, ref.example_index) for ref in parsed.audio_refs] == [
        ("one.mp3", 0),
        ("two.mp3", 1),
    ]


def test_css_adapter_preserves_shared_path_across_audio_semantics():
    parsed = _adapter().parse(
        "hello",
        "<div class='definition'>"
        "<span class='headword-audio' data-src='sound://shared.mp3'></span>"
        "<p class='example'>Example <a class='example-audio' href='sound://shared.mp3'></a></p>"
        "</div>",
    )

    assert [(ref.source_ref, ref.kind, ref.example_index) for ref in parsed.audio_refs] == [
        ("shared.mp3", AudioKind.HEADWORD, None),
        ("shared.mp3", AudioKind.EXAMPLE, 0),
    ]


def test_registry_prefers_requested_then_matching_then_generic():
    first = _adapter("first")
    second = CssSelectorAdapter("second", r"Other", ".definition", None, None, None)
    registry = AdapterRegistry([first, second])

    assert registry.select({"title": "Other"}, requested="first") is first
    assert registry.select({"title": "Other"}) is second
    assert isinstance(registry.select({"title": "Unknown"}), GenericParser)


def test_css_adapter_matches_common_mdict_metadata_casing():
    assert _adapter().matches({"Title": "Demo Dictionary"})
    assert _adapter().matches({"Name": "Demo Dictionary"})
