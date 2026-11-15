from types import SimpleNamespace

from szurubooru.func import translation_jobs as tj

POLY = [[0.1, 0.1], [0.2, 0.1], [0.2, 0.5], [0.1, 0.5]]


def _block(source, translation, bbox=None, color=None):
    block = {"polygon": POLY, "source": source, "translation": translation}
    if bbox is not None:
        block["bbox_px"] = bbox
    if color is not None:
        block["color"] = color
    return block


def test_note_text_plain():
    assert tj.note_text("藍さま", "Ran-sama") == "[MT] Ran-sama"
    assert tj.note_text("藍さま", "Ran-sama", "#E86800") == (
        "[MT]{c:#e86800} Ran-sama"
    )


def test_note_text_untranslated_placeholder():
    text = tj.note_text("藍さま", None, "#e86800")
    assert text == "[MT?]{c:#e86800} 藍さま"
    assert tj.is_mt_placeholder(text)
    assert not tj.is_mt_uncertain(text)


def test_note_text_uncertain():
    text = tj.note_text("「闘れぎ・・・？」", "A dildo...?", "#e86800", True)
    assert text == "[MT?]{c:#e86800} (uncertain) A dildo...?\n「闘れぎ・・・？」"
    assert tj.is_mt_note(text)
    assert tj.is_mt_uncertain(text)
    assert not tj.is_mt_placeholder(text)
    assert tj.is_mt_uncertain(tj.note_text("う", "Ugh", None, True))


def test_note_text_uncertain_without_translation_stays_placeholder():
    text = tj.note_text("のまOO", None, None, True)
    assert text == "[MT?] のまOO"
    assert tj.is_mt_placeholder(text)


def test_plan_notes_flags_only_garbled_blocks():
    blocks = [
        _block("「藍さま藍さまぁ～」", "Ran-sama~", [1286, 14, 1323, 367]),
        _block("うはNo.7つい", "Uha... No. 7...", [1196, 552, 1216, 806]),
        _block("う", "Ugh...", [212, 581, 230, 665], "#e6c600"),
    ]
    to_add, to_remove, skipped = tj.plan_notes([], blocks)
    texts = [note["text"] for note in to_add]
    assert (to_remove, skipped) == ([], 0)
    assert texts == [
        "[MT] Ran-sama~",
        "[MT?] (uncertain) Uha... No. 7...\nうはNo.7つい",
        "[MT?]{c:#e6c600} (uncertain) Ugh...\nう",
    ]


def test_plan_notes_replaces_uncertain_notes():
    existing = [
        (POLY, "[MT?] (uncertain) old\nう"),
        (POLY, "human note"),
    ]
    _to_add, to_remove, _skipped = tj.plan_notes(existing, [])
    assert to_remove == [0]


def _tag(name):
    return SimpleNamespace(names=[SimpleNamespace(name=name)])


def _names(post):
    return sorted(tag.names[0].name for tag in post.tags)


def test_update_tags_adds_and_removes_flags(monkeypatch):
    monkeypatch.setattr(
        tj.tags,
        "get_or_create_tags_by_names",
        lambda names: ([], [_tag(name) for name in names]),
    )
    post = SimpleNamespace(tags=[_tag("reimu_hakurei")])

    created = tj._update_tags(post, incomplete=False, uncertain=True)
    assert [t.names[0].name for t in created] == [
        "machine_translated",
        "translation_uncertain",
    ]
    assert _names(post) == [
        "machine_translated", "reimu_hakurei", "translation_uncertain"
    ]

    tj._update_tags(post, incomplete=True, uncertain=False)
    assert _names(post) == [
        "machine_translated", "reimu_hakurei", "translation_incomplete"
    ]

    tj._update_tags(post, incomplete=False, uncertain=False)
    assert _names(post) == ["machine_translated", "reimu_hakurei"]
