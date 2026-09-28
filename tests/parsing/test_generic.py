from mdict_audio_app.domain.models import AudioKind
from mdict_audio_app.parsing.generic import GenericParser


def test_generic_parser_extracts_and_normalizes_sound_refs():
    parsed = GenericParser().parse(
        "Hello",
        '<div>Hello</div><a href="sound://Audio%2FHELLO.MP3?x=1">play</a>',
    )

    assert parsed.definition_text == "Hello play"
    assert [(ref.source_ref, ref.kind) for ref in parsed.audio_refs] == [
        ("audio/hello.mp3", AudioKind.UNKNOWN)
    ]


def test_generic_parser_deduplicates_attributes():
    parsed = GenericParser().parse(
        "Word",
        "<p>Meaning</p><p>Example one</p><a href='SOUND://foo\\bar.mp3#x'>one</a>"
        "<a href='sound://foo/bar.mp3'>two</a>",
    )

    assert len(parsed.audio_refs) == 1
    assert parsed.audio_refs[0].source_ref == "foo/bar.mp3"


def test_generic_parser_keeps_only_first_sound_ref_per_attribute():
    parsed = GenericParser().parse(
        "Word",
        '<a data-audio="sound://first.mp3 sound://second.mp3">play</a>'
        '<a href="sound://third.mp3">other</a>',
    )

    assert [ref.source_ref for ref in parsed.audio_refs] == ["first.mp3", "third.mp3"]
