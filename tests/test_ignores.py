import unittest

from app import ignores


class EscapeTests(unittest.TestCase):
    def test_roundtrip_special_characters(self):
        for path in ["Dune (2021) [Bluray-2160p]", "The Thing {1982}", "Who? (2000)", "Star*Trek", "back\\slash"]:
            line = ignores.negation_for(path)
            self.assertEqual(ignores.path_from_negation(line), path)

    def test_glob_negations_are_not_paths(self):
        self.assertIsNone(ignores.path_from_negation("!/Movies/*"))
        self.assertIsNone(ignores.path_from_negation("!/Movies/[abc]"))
        self.assertIsNone(ignores.path_from_negation("/Movies"))
        self.assertIsNone(ignores.path_from_negation("!Movies"))


class ParseRenderTests(unittest.TestCase):
    def test_render_then_parse(self):
        user = ["(?d).DS_Store", "*", "// keep me"]
        selected = {"Arrival (2016)", "Severance/Season 01"}
        lines = ignores.render(user, selected)
        self.assertEqual(lines[0], "(?d).DS_Store")
        self.assertEqual(lines[1], "// keep me")  # the bare * was dropped
        self.assertEqual(lines[-1], ignores.END)
        self.assertEqual(lines[-2], "*")
        parsed = ignores.parse(lines)
        self.assertTrue(parsed.managed)
        self.assertEqual(parsed.selected, selected)
        self.assertEqual([l for l in parsed.user_lines if l.strip()], ["(?d).DS_Store", "// keep me"])

    def test_unmanaged(self):
        parsed = ignores.parse(["!/Foo", "*"])
        self.assertFalse(parsed.managed)
        kept, absorbed = ignores.absorb_plain_negations(parsed.user_lines)
        self.assertEqual(absorbed, {"Foo"})
        self.assertEqual(kept, ["*"])
        self.assertNotIn("*", ignores.render(kept, absorbed)[:-3])

    def test_normalize_and_coverage(self):
        sel = ignores.normalize_selection({"Show", "Show/Season 01", "Other/Season 02"})
        self.assertEqual(sel, {"Show", "Other/Season 02"})
        self.assertTrue(ignores.is_covered("Show/Season 05", sel))
        self.assertFalse(ignores.is_covered("Other", sel))
        self.assertTrue(ignores.has_selected_descendant("Other", sel))


if __name__ == "__main__":
    unittest.main()
