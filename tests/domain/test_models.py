from mdict_audio_app.domain.models import AudioSelectionSettings, normalize_headword


def test_normalize_headword_unifies_case_width_and_space():
    assert normalize_headword("  Ｃafé\t") == "café"


def test_selection_settings_reject_negative_limits():
    try:
        AudioSelectionSettings(max_examples=-1)
    except ValueError as exc:
        assert "max_examples" in str(exc)
    else:
        raise AssertionError("negative max_examples was accepted")
