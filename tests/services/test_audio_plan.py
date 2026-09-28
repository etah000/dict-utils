from dataclasses import replace
from pathlib import Path

from mdict_audio_app.domain import AudioKind, AudioSelectionSettings
from mdict_audio_app.services.audio_plan import AudioPlanBuilder, DictionaryAudioProvider, parse_word_file, parse_word_list
from mdict_audio_app.services.dictionary_query import DictionaryExample, ResolvedAudio, WordSelection


def test_parse_word_list_deduplicates_case_width_and_whitespace():
    assert parse_word_list(" Alpha\n\uff21\uff2c\uff30\uff28\uff21\n beta \n\n") == ("Alpha", "beta")


def test_parse_word_file_supports_utf8_bom_txt_and_first_csv_column(tmp_path: Path):
    txt = tmp_path / "words.TXT"
    txt.write_text("\ufeffone\nTwo\n", encoding="utf-8")
    csv = tmp_path / "words.CSV"
    csv.write_text("\ufeffOne,ignored\nthree,definition\n", encoding="utf-8")
    assert parse_word_file(txt) == ("one", "Two")
    assert parse_word_file(csv) == ("One", "three")


class FakeProvider:
    def __init__(self, selections):
        self.selections = selections

    def resolve(self, word, settings):
        return self.selections.get(word.casefold())


def _selection(word="Alpha", *, headword=True, examples=(100, 100)):
    example_rows = tuple(DictionaryExample(i + 1, 1, 7, f"{word} example {i}", i) for i in range(len(examples)))
    links = []
    if headword:
        links.append(ResolvedAudio(11, word, AudioKind.HEADWORD, 101, 500, 7, None, "head.mp3", 0))
    for index, duration in enumerate(examples):
        links.append(ResolvedAudio(20 + index, word, AudioKind.EXAMPLE, 200 + index, duration, 7, index + 1, f"example-{index}.mp3", index, example_rows[index].text))
    return WordSelection(word, 7, (), example_rows, links[0] if headword else None, tuple(links))


def test_plan_limits_examples_and_duration_without_cross_word_examples():
    provider = FakeProvider({"alpha": _selection(), "beta": _selection("beta")})
    words = parse_word_list("Alpha\nbeta\nALPHA\n")
    plan = AudioPlanBuilder(provider).build(words, AudioSelectionSettings(max_examples=1, max_total_ms=1_000, clip_gap_ms=100, word_gap_ms=200))
    assert words == ("Alpha", "beta")
    assert [clip.word for clip in plan.clips] == ["Alpha", "Alpha"]
    assert [clip.kind for clip in plan.clips] == [AudioKind.HEADWORD, AudioKind.EXAMPLE]
    assert plan.total_audio_ms == 700
    assert ("beta", "\u8d85\u8fc7\u6700\u5927\u603b\u65f6\u957f") in plan.skipped


def test_plan_records_chinese_missing_reasons():
    provider = FakeProvider({"silent": _selection("silent", headword=False, examples=())})
    plan = AudioPlanBuilder(provider).build(("unknown", "silent"), AudioSelectionSettings())
    assert plan.clips == ()
    assert ("unknown", "\u7f3a\u5c11\u8bcd\u6761") in plan.skipped
    assert ("silent", "\u7f3a\u5c11\u8bcd\u5934\u53d1\u97f3") in plan.skipped


def test_plan_uses_valid_later_example_when_first_reference_is_missing():
    selection = _selection("later", examples=(100, 200))
    links = list(selection.audio_links)
    links[1] = replace(links[1], example_id=999)
    later = replace(selection, audio_links=tuple(links))
    plan = AudioPlanBuilder(FakeProvider({"later": later})).build(("later",), AudioSelectionSettings(max_examples=1))
    assert [clip.resource_id for clip in plan.clips] == [101, 201]


def test_plan_stops_processing_words_after_duration_limit():
    provider = FakeProvider({"alpha": _selection(), "beta": _selection("beta"), "gamma": _selection("gamma")})
    plan = AudioPlanBuilder(provider).build(("alpha", "beta", "gamma"), AudioSelectionSettings(max_examples=0, max_total_ms=600, word_gap_ms=200))
    assert [clip.word for clip in plan.clips] == ["alpha"]
    assert [word for word, _ in plan.skipped] == ["beta"]


def test_zero_example_limit_keeps_headword_without_missing_example_reason():
    plan = AudioPlanBuilder(FakeProvider({"alpha": _selection("alpha")})).build(
        ("alpha",), AudioSelectionSettings(max_examples=0)
    )
    assert len(plan.clips) == 1
    assert plan.clips[0].kind is AudioKind.HEADWORD
    assert all(reason != "\u7f3a\u5c11\u4f8b\u53e5\u97f3\u9891" for _, reason in plan.skipped)


def test_dictionary_provider_delegates_query_and_fallback_setting():
    class FakeQuery:
        def __init__(self):
            self.calls = []

        def lookup(self, word, fallback_headword_audio=True):
            self.calls.append((word, fallback_headword_audio))
            return _selection(word)

    query = FakeQuery()
    result = DictionaryAudioProvider(query).resolve("Alpha", AudioSelectionSettings(fallback_headword_audio=False))
    assert result.word == "Alpha"
    assert query.calls == [("Alpha", False)]
