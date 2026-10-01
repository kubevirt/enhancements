import unittest

from front_matter import extract_front_matter, is_meta_vep


class ExtractFrontMatterTest(unittest.TestCase):
    def test_none_input(self):
        self.assertIsNone(extract_front_matter(None))

    def test_no_front_matter(self):
        self.assertIsNone(extract_front_matter("# Just a heading\n\nBody text.\n"))

    def test_unterminated_block(self):
        self.assertIsNone(extract_front_matter("---\ntitle: Foo\n"))

    def test_valid_front_matter(self):
        text = "---\ntitle: Foo\nvep-number: 42\n---\n\n# Foo\n"
        self.assertEqual(
            extract_front_matter(text), {"title": "Foo", "vep-number": 42}
        )

    def test_invalid_yaml(self):
        text = "---\ntitle: [unclosed\n---\n\n# Foo\n"
        self.assertEqual(extract_front_matter(text), "invalid-yaml")

    def test_empty_block(self):
        text = "---\n---\n\n# Foo\n"
        self.assertEqual(extract_front_matter(text), {})

    def test_an_indented_dashes_line_in_a_block_scalar_is_not_mistaken_for_the_closing_delimiter(self):
        # A multi-line value can legitimately contain an indented "---" (e.g. a markdown
        # horizontal rule inside a `description: |` block). Only a delimiter at column 0
        # should close the front matter block; stripping leading whitespace before
        # comparing would close it early here, silently dropping `vep-number`.
        text = (
            "---\n"
            "title: Foo\n"
            "description: |\n"
            "  Some text\n"
            "  ---\n"
            "  more text\n"
            "vep-number: 42\n"
            "---\n\n# Foo\n"
        )
        fm = extract_front_matter(text)
        self.assertEqual(fm["vep-number"], 42)
        self.assertIn("---", fm["description"])


class IsMetaVepTest(unittest.TestCase):
    def test_meta_vep(self):
        self.assertTrue(is_meta_vep("veps/meta-VEPs/282-x/vep.md"))

    def test_regular_vep(self):
        self.assertFalse(is_meta_vep("veps/sig-compute/10-x/vep.md"))


if __name__ == "__main__":
    unittest.main()
