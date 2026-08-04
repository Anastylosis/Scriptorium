import pytest

from scriptorium import langs


@pytest.mark.parametrize("code", ["en", "es", "pt", "zh", "cy", "eng", "por", "deu", "yue"])
def test_accepts_bare_two_and_three_letter_codes(code):
    assert langs.is_caption_suffix(code)


@pytest.mark.parametrize("code", ["en-US", "pt-BR", "sr-Latn", "zh-Hans", "en_US"])
def test_rejects_regional_and_script_subtags(code):
    # Stash parses the caption suffix with ParseBase, which takes a bare
    # subtag. A regional one does not parse, so the suffix is treated as part
    # of the filename and the caption never attaches to the scene.
    assert not langs.is_caption_suffix(code)
    reason = langs.reject_reason(code)
    assert "regional subtag" in reason
    assert "never attaches" in reason


def test_regional_rejection_suggests_the_base_code():
    assert "Use subs:pt." in langs.reject_reason("pt-BR")
    assert "Use subs:en." in langs.reject_reason("en-US")


@pytest.mark.parametrize("code", ["xx", "zzz", "e", "abcd", "123", "en1", ""])
def test_rejects_codes_that_are_not_languages(code):
    assert not langs.is_caption_suffix(code)


def test_unknown_code_rejection_does_not_mention_subtags():
    assert "not an ISO 639 language code" in langs.reject_reason("xx")


@pytest.mark.parametrize("raw,expected", [
    ("EN", "en"), (" en ", "en"), ("Eng", "en"), ("POR", "pt"), ("deu", "de"),
])
def test_normalize_folds_case_and_three_letter_aliases(raw, expected):
    assert langs.normalize(raw) == expected


def test_equivalence_across_code_lengths():
    assert langs.equivalent("en", "eng")
    assert langs.equivalent("EN", "en")
    assert not langs.equivalent("en", "es")


def test_stash_unknown_language_marker_never_matches_a_real_code():
    # Stash files a caption with no language suffix as "00".
    assert not langs.equivalent("en", "00")
    assert not langs.equivalent("en", "")
    assert not langs.equivalent("en", None)


def test_caption_validity_and_whisper_support_are_different_questions():
    # fil is a legal caption suffix Whisper has never heard of; it has tl.
    # Collapsing these into one predicate would report a real language as
    # invalid, so they are asserted together to stop that happening.
    assert langs.is_caption_suffix("fil")
    assert not langs.whisper_supports("fil")
    assert langs.whisper_supports("tl")
    assert langs.nearest_whisper("fil") == "tl"


def test_whisper_supports_the_common_cases():
    for code in ("en", "es", "de", "ja", "pl", "zh"):
        assert langs.whisper_supports(code), code


def test_name_of():
    assert langs.name_of("en") == "English"
    assert langs.name_of("eng") == "English"
    assert langs.name_of("pt") == "Portuguese"
    # Unknown codes come back unchanged so a prompt still reads sensibly.
    assert langs.name_of("qq") == "qq"
