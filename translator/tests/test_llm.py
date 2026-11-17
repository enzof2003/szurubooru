"""LLM reply handling (pipeline.Translator): parsing, the retry rules, what is sent.

No network and no models: the HTTP session is a fake that plays scripted replies.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402
from PIL import Image  # noqa: E402

import pipeline  # noqa: E402


def reply(translations=None, content=None, status=200, usage=None, body=None, text=""):
    """A scripted HTTP reply. `translations`: {id: text} -> a well-formed answer."""
    if content is None and translations is not None:
        content = json.dumps({"translations": [{"id": i, "text": t} for i, t in translations.items()]})
    if body is None:
        body = {"choices": [{"message": {"content": content}}], "model": "fake-model",
                "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}}
    return SimpleNamespace(status_code=status, text=text, json=lambda: body)


class FakeSession:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        # deep copy: _call mutates its body between attempts
        self.calls.append(SimpleNamespace(url=url, body=globals()["json"].loads(globals()["json"].dumps(json)),
                                          headers=headers, timeout=timeout))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def segments(call) -> list[dict]:
    user = call.body["messages"][1]["content"]
    text = user if isinstance(user, str) else user[0]["text"]
    return json.loads(text)["segments"]


class LlmCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.s = SimpleNamespace(
            glossary_file=root / "glossary.txt", characters_file=root / "characters.txt",
            instructions_file=root / "instructions.txt", target_language="English", vision=False,
            llm_model="test-model", llm_temperature=0.3, llm_max_tokens=1000, reasoning=False,
            llm_json_mode=True, llm_extra_body={}, llm_api_key="", llm_base_url="http://llm/v1",
            llm_timeout=30, vision_max_side=64)
        self.img = Image.new("RGB", (200, 100), "white")
        self.blocks = [{"source": "こんにちは", "box": [0, 0, 100, 50]},
                       {"source": "ありがとう", "box": [100, 50, 200, 100]},
                       {"source": "さようなら", "box": [20, 10, 40, 30]}]

    def translator(self, *replies):
        t = pipeline.Translator(self.s)
        t.session = FakeSession(replies)
        return t


class ParseTest(unittest.TestCase):
    parse = staticmethod(pipeline.Translator._parse)

    def test_plain_object(self):
        self.assertEqual(self.parse('{"translations": [{"id": 0, "text": "Hello"}, {"id": 1, "text": "Thanks"}]}'),
                         {0: "Hello", 1: "Thanks"})

    def test_markdown_fence(self):
        for fenced in ('```json\n{"translations": [{"id": 0, "text": "Hello"}]}\n```',
                       '```\n{"translations": [{"id": 0, "text": "Hello"}]}```',
                       '  ```json {"translations": [{"id": 0, "text": "Hello"}]} ```  '):
            self.assertEqual(self.parse(fenced), {0: "Hello"}, fenced)

    def test_commentary_around_the_object(self):
        self.assertEqual(self.parse('Sure! Here it is:\n{"translations": [{"id": 0, "text": "Hello"}]}\nHope it helps.'),
                         {0: "Hello"})

    def test_bare_list(self):
        self.assertEqual(self.parse('[{"id": 2, "text": "Bye"}]'), {2: "Bye"})

    def test_null_text_is_missing_not_the_string_none(self):
        self.assertEqual(self.parse('{"translations": [{"id": 0, "text": null}, {"id": 1, "text": "Thanks"}]}'),
                         {1: "Thanks"})

    def test_ids_as_strings_and_text_as_numbers(self):
        self.assertEqual(self.parse('{"translations": [{"id": "1", "text": 5}]}'), {1: "5"})

    def test_bad_items_are_skipped_not_fatal(self):
        raw = json.dumps({"translations": [
            {"id": 0, "text": "Hello"}, {"id": "x", "text": "bad id"}, {"id": None, "text": "no id"},
            {"text": "id missing"}, {"id": 2}, "a string", 7, None, ["list"], {"id": 1, "text": "Thanks"}]})
        self.assertEqual(self.parse(raw), {0: "Hello", 1: "Thanks"})

    def test_last_duplicate_id_wins(self):
        self.assertEqual(self.parse('[{"id": 0, "text": "a"}, {"id": 0, "text": "b"}]'), {0: "b"})

    def test_object_without_translations_gives_nothing(self):
        self.assertEqual(self.parse('{"result": "ok"}'), {})
        self.assertEqual(self.parse('{"translations": []}'), {})

    def test_leaked_json_closers_stripped_from_text(self):
        raw = json.dumps({"translations": [{"id": 0, "text": "Hello!\"}]}}]}"}, {"id": 1, "text": "[an aside]"}]})
        self.assertEqual(self.parse(raw), {0: 'Hello!"', 1: "[an aside]"})

    def test_text_is_stripped_but_kept_when_empty(self):
        # "" is dropped later, by _call (so that the segment is retried)
        self.assertEqual(self.parse('[{"id": 0, "text": "  Hello \\n"}, {"id": 1, "text": "   "}]'), {0: "Hello", 1: ""})

    def test_malformed_json_raises(self):
        for raw in ("", "I can't help with that.", '{"translations": [{"id": 0, "text": "Hel', "{]", "```json\n```"):
            with self.assertRaises(json.JSONDecodeError, msg=raw):
                self.parse(raw)

    def test_truncated_reply_is_not_half_accepted(self):
        # cut off by max_tokens: the object never closes -> error (-> retry), never a partial page
        raw = '{"translations": [{"id": 0, "text": "Hello"}, {"id": 1, "text": "Tha'
        with self.assertRaises(json.JSONDecodeError):
            self.parse(raw)

    def test_non_iterable_translations_raise_a_type_error(self):
        for raw in ('{"translations": 5}', '{"translations": null}', "5", "null", "true"):
            with self.assertRaises(TypeError, msg=raw):
                self.parse(raw)


class CallTest(LlmCase):
    def test_success(self):
        t = self.translator(reply({0: "Hello", 1: "Thanks", 2: "Bye"}))
        got, meta = t._call(self.img, self.blocks, [0, 1, 2])
        self.assertEqual(got, {0: "Hello", 1: "Thanks", 2: "Bye"})
        self.assertEqual(meta, {"usage": {"prompt_tokens": 10, "completion_tokens": 5}, "model": "fake-model",
                                "attempts": 1})
        (call,) = t.session.calls
        self.assertEqual(call.url, "http://llm/v1/chat/completions")
        self.assertEqual(call.timeout, 30)

    def test_request_body(self):
        t = self.translator(reply({0: "Hello"}))
        t._call(self.img, self.blocks, [0, 2])
        body = t.session.calls[0].body
        self.assertEqual((body["model"], body["temperature"], body["max_tokens"], body["stream"]),
                         ("test-model", 0.3, 1000, False))
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["reasoning"], {"enabled": False})
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertIsInstance(body["messages"][1]["content"], str)  # no vision: plain text
        # only the requested ids, keeping their page-wide numbers; boxes on a 0-1000 scale
        self.assertEqual(segments(t.session.calls[0]), [
            {"id": 0, "text": "こんにちは", "box": [0, 0, 500, 500]},
            {"id": 2, "text": "さようなら", "box": [100, 100, 200, 300]}])

    def test_optional_settings(self):
        self.s.llm_api_key = "llm-key"
        self.s.reasoning = True
        self.s.llm_json_mode = False
        self.s.llm_extra_body = {"provider": {"sort": "throughput"}, "temperature": 0}
        t = self.translator(reply({0: "Hello"}))
        t._call(self.img, self.blocks, [0])
        call = t.session.calls[0]
        self.assertEqual(call.headers["Authorization"], "Bearer llm-key")
        self.assertNotIn("response_format", call.body)
        self.assertNotIn("reasoning", call.body)
        self.assertNotIn("chat_template_kwargs", call.body)
        self.assertEqual(call.body["provider"], {"sort": "throughput"})
        self.assertEqual(call.body["temperature"], 0)  # LLM_EXTRA_BODY wins

    def test_no_authorization_header_without_a_key(self):
        t = self.translator(reply({0: "Hello"}))
        t._call(self.img, self.blocks, [0])
        self.assertNotIn("Authorization", t.session.calls[0].headers)

    def test_vision_sends_a_downscaled_page(self):
        self.s.vision = True
        t = self.translator(reply({0: "Hello"}))
        t._call(self.img, self.blocks, [0])
        text, image = t.session.calls[0].body["messages"][1]["content"]
        self.assertEqual(text["type"], "text")
        self.assertTrue(image["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        import base64
        import io
        sent = Image.open(io.BytesIO(base64.b64decode(image["image_url"]["url"].split(",", 1)[1])))
        self.assertEqual(sent.size, (64, 32))  # longest side = VISION_MAX_SIDE

    def test_speaker_letters_are_sent_and_explained(self):
        self.blocks[1]["speaker"] = "B"
        t = self.translator(reply({0: "Hello", 1: "Thanks"}), reply({0: "Hello"}))
        t._call(self.img, self.blocks, [0, 1])
        self.assertEqual([s.get("speaker") for s in segments(t.session.calls[0])], [None, "B"])
        self.assertIn('"speaker" letter', t.session.calls[0].body["messages"][0]["content"])
        t._call(self.img, self.blocks, [0])  # no speaker among the requested segments
        self.assertNotIn('"speaker" letter', t.session.calls[1].body["messages"][0]["content"])

    def test_glossary_instructions_and_context_are_repeated_in_the_user_message(self):
        self.s.glossary_file.write_text("# comment\nこんにちは = Hello there\n\n別の語 = Unused\nbroken line\n = x\ny = \n",
                                        encoding="utf-8")
        self.s.instructions_file.write_text("Be brief.\n", encoding="utf-8")
        t = self.translator(reply({0: "Hello there"}))
        t._call(self.img, self.blocks, [0, 1], context="Characters/series in this image: characters: someone")
        system, user = t.session.calls[0].body["messages"]
        payload = json.loads(user["content"])
        self.assertEqual(list(payload), ["instructions", "context", "segments", "glossary"])
        self.assertEqual(payload["glossary"], {"こんにちは": "Hello there"})  # only terms on this page
        self.assertEqual(payload["instructions"], "Be brief.")
        for needle in ("こんにちは = Hello there", "Be brief.", "characters: someone"):
            self.assertIn(needle, system["content"])
        self.assertNotIn("Unused", system["content"])

    def test_nothing_extra_without_glossary_instructions_or_context(self):
        t = self.translator(reply({0: "Hello"}))
        t._call(self.img, self.blocks, [0])
        self.assertEqual(list(json.loads(t.session.calls[0].body["messages"][1]["content"])), ["segments"])

    def test_unknown_ids_and_empty_texts_are_dropped(self):
        t = self.translator(reply({0: "Hello", 1: "", 2: "   ", 7: "who?", -1: "x"}))
        got, _ = t._call(self.img, self.blocks, [0, 1, 2])
        self.assertEqual(got, {0: "Hello"})

    def test_ids_not_asked_for_are_dropped(self):
        t = self.translator(reply({0: "Hello", 1: "Thanks"}))
        got, _ = t._call(self.img, self.blocks, [1])
        self.assertEqual(got, {1: "Thanks"})

    def test_malformed_reply_is_retried_once(self):
        t = self.translator(reply(content="Sorry, I"), reply({0: "Hello"}, usage={"prompt_tokens": 7, "total": 1.5}))
        got, meta = t._call(self.img, self.blocks, [0])
        self.assertEqual(got, {0: "Hello"})
        self.assertEqual(meta["attempts"], 2)
        # both attempts are paid for
        self.assertEqual(meta["usage"], {"prompt_tokens": 17, "completion_tokens": 5, "total": 1.5})
        self.assertEqual(t.session.calls[0].body, t.session.calls[1].body)  # the same request again

    def test_two_malformed_replies_are_an_error(self):
        t = self.translator(reply(content="nope"), reply(content="still nope"))
        with self.assertRaises(pipeline.LlmError) as ctx:
            t._call(self.img, self.blocks, [0])
        self.assertIn("unparseable", str(ctx.exception))
        self.assertEqual(len(t.session.calls), 2)  # never a third attempt

    def test_malformed_shapes_that_count_as_unparseable(self):
        shapes = [
            reply(body={}),                                          # no choices
            reply(body={"choices": []}),
            reply(body={"choices": [{}]}),                           # no message
            reply(body={"choices": [{"message": None}]}),
            reply(body=["not", "an", "object"]),
            reply(content=None),                                     # content null (e.g. only reasoning)
            reply(content=""),
            reply(content='{"translations": 5}'),
            reply(content="null"),
        ]
        for bad in shapes:
            t = self.translator(bad, reply({0: "Hello"}))
            got, meta = t._call(self.img, self.blocks, [0])
            self.assertEqual((got, meta["attempts"]), ({0: "Hello"}, 2))

    def test_non_json_http_body_is_retried(self):
        def not_json():
            raise requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)

        bad = SimpleNamespace(status_code=200, text="<html>", json=not_json)
        t = self.translator(bad, reply({0: "Hello"}))
        got, meta = t._call(self.img, self.blocks, [0])
        self.assertEqual((got, meta["attempts"]), ({0: "Hello"}, 2))

    def test_valid_but_empty_reply_is_not_retried_here(self):
        # a well-formed answer with nothing usable: _call returns {}; translate() decides about a retry
        t = self.translator(reply({}))
        got, meta = t._call(self.img, self.blocks, [0, 1])
        self.assertEqual((got, meta["attempts"]), ({}, 1))
        self.assertEqual(len(t.session.calls), 1)

    def test_http_400_retries_without_json_mode(self):
        t = self.translator(reply(status=400, text="response_format not supported"), reply({0: "Hello"}))
        got, meta = t._call(self.img, self.blocks, [0])
        self.assertEqual(got, {0: "Hello"})
        self.assertIn("response_format", t.session.calls[0].body)
        self.assertNotIn("response_format", t.session.calls[1].body)
        self.assertEqual(meta["attempts"], 2)

    def test_http_400_twice_is_an_error_with_the_providers_text(self):
        t = self.translator(reply(status=400, text="bad"), reply(status=400, text="context too long " + "x" * 600))
        with self.assertRaises(pipeline.LlmError) as ctx:
            t._call(self.img, self.blocks, [0])
        self.assertTrue(str(ctx.exception).startswith("LLM HTTP 400: context too long"))
        self.assertLessEqual(len(str(ctx.exception)), len("LLM HTTP 400: ") + 500)

    def test_http_400_without_json_mode_is_an_error_at_once(self):
        self.s.llm_json_mode = False
        t = self.translator(reply(status=400, text="bad request"))
        with self.assertRaises(pipeline.LlmError):
            t._call(self.img, self.blocks, [0])
        self.assertEqual(len(t.session.calls), 1)

    def test_other_http_errors_are_not_retried(self):
        for status in (401, 402, 404, 429, 500, 502, 503):
            t = self.translator(reply(status=status, text="boom"), reply({0: "Hello"}))
            with self.assertRaises(pipeline.LlmError) as ctx:
                t._call(self.img, self.blocks, [0])
            self.assertEqual(str(ctx.exception), f"LLM HTTP {status}: boom")
            self.assertEqual(len(t.session.calls), 1, status)

    def test_network_errors_are_not_retried(self):
        for exc in (requests.ConnectionError("refused"), requests.Timeout("slow")):
            t = self.translator(exc, reply({0: "Hello"}))
            with self.assertRaises(pipeline.LlmError) as ctx:
                t._call(self.img, self.blocks, [0])
            self.assertIn("LLM request failed", str(ctx.exception))
            self.assertEqual(len(t.session.calls), 1)

    def test_malformed_then_http_400_uses_up_both_attempts(self):
        """Current behaviour, reported as a finding: a 400 on the second attempt drops json mode
        but there is no attempt left to use it, and the error names the first reply's problem."""
        t = self.translator(reply(content="nope"), reply(status=400, text="response_format not supported"),
                            reply({0: "Hello"}))
        with self.assertRaises(pipeline.LlmError) as ctx:
            t._call(self.img, self.blocks, [0])
        self.assertIn("unparseable", str(ctx.exception))
        self.assertEqual(len(t.session.calls), 2)

    def test_model_name_falls_back_to_the_configured_one(self):
        body = {"choices": [{"message": {"content": '{"translations": [{"id": 0, "text": "Hello"}]}'}}]}
        t = self.translator(reply(body=body))
        _, meta = t._call(self.img, self.blocks, [0])
        self.assertEqual(meta, {"usage": {}, "model": "test-model", "attempts": 1})

    def test_non_numeric_usage_fields_are_ignored(self):
        t = self.translator(reply({0: "Hello"}, usage={"prompt_tokens": 3, "details": {"cached": 1}, "note": "x"}))
        _, meta = t._call(self.img, self.blocks, [0])
        self.assertEqual(meta["usage"], {"prompt_tokens": 3})


class TranslateTest(LlmCase):
    """translate(): one call for the page, then at most one more for what is missing."""

    def test_everything_in_one_call(self):
        t = self.translator(reply({0: "Hello", 1: "Thanks", 2: "Bye"}))
        out, meta = t.translate(self.img, self.blocks)
        self.assertEqual(out, ["Hello", "Thanks", "Bye"])
        self.assertEqual((meta["retried_ids"], meta["missing_ids"], meta["attempts"]), ([], [], 1))
        self.assertNotIn("context", meta)
        self.assertEqual(len(t.session.calls), 1)

    def test_missing_segments_get_one_retry_on_their_own(self):
        t = self.translator(reply({0: "Hello", 2: "Bye"}), reply({1: "Thanks"}))
        out, meta = t.translate(self.img, self.blocks)
        self.assertEqual(out, ["Hello", "Thanks", "Bye"])
        self.assertEqual((meta["retried_ids"], meta["missing_ids"]), ([1], []))
        self.assertEqual([s["id"] for s in segments(t.session.calls[1])], [1])  # only the missing one, same id
        self.assertEqual(meta["attempts"], 2)
        self.assertEqual(meta["usage"], {"prompt_tokens": 20, "completion_tokens": 10})

    def test_null_and_empty_translations_count_as_missing(self):
        first = reply(content=json.dumps({"translations": [
            {"id": 0, "text": None}, {"id": 1, "text": ""}, {"id": 2, "text": "Bye"}]}))
        t = self.translator(first, reply({0: "Hello", 1: "Thanks"}))
        out, meta = t.translate(self.img, self.blocks)
        self.assertEqual(out, ["Hello", "Thanks", "Bye"])
        self.assertEqual(meta["retried_ids"], [0, 1])

    def test_still_missing_after_the_retry_is_none_never_an_empty_string(self):
        t = self.translator(reply({0: "Hello"}), reply({1: "Thanks", 2: None}))
        out, meta = t.translate(self.img, self.blocks)
        self.assertEqual(out, ["Hello", "Thanks", None])
        self.assertEqual((meta["retried_ids"], meta["missing_ids"]), ([1, 2], [2]))
        self.assertEqual(len(t.session.calls), 2)  # one retry, not one per missing segment, and no third round

    def test_retry_cannot_overwrite_with_other_ids(self):
        t = self.translator(reply({0: "Hello"}), reply({0: "CHANGED", 1: "Thanks", 2: "Bye"}))
        out, _ = t.translate(self.img, self.blocks)
        self.assertEqual(out, ["Hello", "Thanks", "Bye"])

    def test_nothing_usable_at_all(self):
        t = self.translator(reply({}), reply({}))
        out, meta = t.translate(self.img, self.blocks)
        self.assertEqual(out, [None, None, None])
        self.assertEqual(meta["missing_ids"], [0, 1, 2])

    def test_failed_retry_keeps_the_first_answer(self):
        t = self.translator(reply({0: "Hello", 2: "Bye"}), reply(status=500, text="down"))
        out, meta = t.translate(self.img, self.blocks)
        self.assertEqual(out, ["Hello", None, "Bye"])
        self.assertEqual(meta["retry_error"], "LLM HTTP 500: down")
        self.assertEqual((meta["missing_ids"], meta["attempts"]), ([1], 1))

    def test_failed_first_call_is_an_error(self):
        for replies in ([reply(status=500, text="down")], [reply(content="x"), reply(content="y")],
                        [requests.ConnectionError("refused")]):
            t = self.translator(*replies)
            with self.assertRaises(pipeline.LlmError):
                t.translate(self.img, self.blocks)

    def test_at_most_four_http_requests_per_page(self):
        # malformed + ok (2 attempts), then the retry: malformed + ok (2 attempts)
        t = self.translator(reply(content="x"), reply({0: "Hello"}), reply(content="y"), reply({1: "Thanks", 2: "Bye"}))
        out, meta = t.translate(self.img, self.blocks)
        self.assertEqual(out, ["Hello", "Thanks", "Bye"])
        self.assertEqual((meta["attempts"], len(t.session.calls)), (4, 4))

    def test_context_from_tags(self):
        self.s.characters_file.write_text("Some_Character = a shrine maiden\nother = unused\n", encoding="utf-8")
        t = self.translator(reply({0: "Hello", 1: "Thanks", 2: "Bye"}))
        _, meta = t.translate(self.img, self.blocks, {"character": ["some_character", "someone_else"],
                                                      "series": ["a_series"], "artist": ["an_artist"]})
        self.assertEqual(meta["context"].splitlines(), [
            "Characters/series in this image: characters: some character, someone else; series: a series",
            "Artist: an artist",
            "Character notes (who they are; use them to recognise puns and lines that are literal "
            "for that character, but don't force them into lines they don't fit):",
            "- a shrine maiden"])
        self.assertEqual(json.loads(t.session.calls[0].body["messages"][1]["content"])["context"], meta["context"])

    def test_no_blocks(self):
        t = self.translator(reply({}))
        out, meta = t.translate(self.img, [])
        self.assertEqual((out, meta["retried_ids"], meta["missing_ids"]), ([], [], []))


if __name__ == "__main__":
    unittest.main()
