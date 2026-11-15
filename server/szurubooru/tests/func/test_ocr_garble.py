import pytest

from szurubooru.func import ocr_garble

# Real OCR output from posts tagged translation_error (2026-11-15), with the
# block's bbox_px where a rule depends on it.
GARBLED = [
    ("」をふら～♡も～♡はんきむやたったらあ～♡「", None, "reversed-quote"),
    (
        "ちゅりーさまあ…ツ」て呼ばないの？代用品なのでしょう…?「リーがいいの"
        "おッ…♡」んだ忠臣だこと♡イドが聞いて呆れるわぁ♡",
        None,
        "orphan-text",
    ),
    ("うはNo.7つい", None, "latin-in-kana"),
    ("のまOO", [1268, 419, 1277, 557], "latin-in-kana"),
    ("「♡…は・・・し・・・い、す」", None, "isolated-kana"),
    ("う", [212, 581, 230, 665], "thin-fragment"),
    ("スけ", [39, 583, 51, 639], "thin-fragment"),
    ("（…ひゃっ）", [1141, 14, 1187, 720], "thin-fragment"),
]

READABLE = [
    ("「藍さま藍さまぁ～」", [1286, 14, 1323, 367]),
    ("「あ…あぅ...鈴仙さん...れぇ...せん...さ...ん...」", [1286, 9, 1329, 848]),
    ("「んッ…♡あぁツ…♥す...ご...お...ツ♡」", [1285, 9, 1328, 727]),
    ("す・て・き♡", None),
    ("ハ～イ！すぐイキま～す♡", None),
    ("ふーふのいとなみのジャマだぞーでーなーおーせー", None),
    ("ザーコザーコWW", None),
    ("ほッ！腸内丸見えｗ", None),
    ("も、もう一度〇に･･･", None),
    ("かわいいからこんなになっちゃいました…♥」「いやッ…近づけないで...ツ」", None),
    ("「えっと…あれは…その…」本当は飲み薬なんでしょコレ！「…え？」", None),
    ("さんがあまりにも", [1054, 350, 1089, 642]),
    ("ん…", [828, 28, 865, 106]),
    ("あー", [1103, 704, 1133, 886]),  # long bar drawn longer than 2 cells
    ("そしてー", [1134, 210, 1162, 467]),
    ("ドキドキ", None),
    ("ゴメン、早苗、諏訪子……", [539, 539, 706, 774]),
    ("", None),
    (None, None),
    ("……！？", [0, 0, 5, 500]),
]


@pytest.mark.parametrize("source,bbox,reason", GARBLED)
def test_garbled(source, bbox, reason):
    assert reason in ocr_garble.garble_reasons(source, bbox)
    assert ocr_garble.is_garbled(source, bbox)


@pytest.mark.parametrize("source,bbox", READABLE)
def test_readable(source, bbox):
    assert ocr_garble.garble_reasons(source, bbox) == []


@pytest.mark.parametrize("bbox", [None, "junk", [1, 2, 3], [5, 5, 5, 5]])
def test_bad_bbox_is_ignored(bbox):
    assert ocr_garble.garble_reasons("う", bbox) == []
