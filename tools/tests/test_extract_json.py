"""llm.extract_json: one reader for JSON in model replies (2026-10-08)."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
from llm import extract_json, iter_json


class ExtractJsonTests(unittest.TestCase):
    def test_bare_object_and_list(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})
        self.assertEqual(extract_json('[1, 2]', list), [1, 2])
        self.assertIsNone(extract_json('{"a": 1}', list))
        self.assertIsNone(extract_json(None))
        self.assertIsNone(extract_json("no json here"))

    def test_prose_fence_and_think_block(self):
        reply = '<think>{"draft": true}</think>Sure, here it is:\n```json\n{"verdict": "link"}\n```\nDone.'
        self.assertEqual(extract_json(reply), {"verdict": "link"})

    def test_brace_in_prose_does_not_break_the_read(self):
        reply = 'Note the {braces} here. Result: {"ok": true, "n": [1, {"x": 2}]} trailing'
        self.assertEqual(extract_json(reply), {"ok": True, "n": [1, {"x": 2}]})

    def test_two_objects_are_not_merged_by_a_greedy_match(self):
        reply = '{"first": 1}\nand then\n{"second": 2}'
        self.assertEqual(extract_json(reply), {"first": 1})
        self.assertEqual(extract_json(reply, last=True), {"second": 2})
        self.assertEqual(list(iter_json(reply)), [{"first": 1}, {"second": 2}])

    def test_list_of_objects_with_nested_brackets(self):
        reply = 'Ideas:\n[{"title": "a [1]", "sources": ["x", "y"]}, {"title": "b"}]'
        self.assertEqual(len(extract_json(reply, list)), 2)

    def test_truncated_json_is_none(self):
        self.assertIsNone(extract_json('{"verdict": "link", "reason": "cut off'))


if __name__ == "__main__":
    unittest.main()
