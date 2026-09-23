import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path("scripts/generate_audio.py")
spec = importlib.util.spec_from_file_location("generate_audio", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class PronunciationMatchingTests(unittest.TestCase):
    def test_edge_aliases_do_not_replace_inside_spanish_words(self):
        aliases = {
            "CI": "si-ái",
            "CD": "si-dí",
            "API": "éi-pi-ái",
            "AI": "éi-ái",
        }
        source = "La petición y la decisión mejoran la capacidad del sistema."
        self.assertEqual(module.apply_edge_aliases(source, aliases), source)

    def test_edge_aliases_replace_complete_technical_terms(self):
        aliases = {
            "CI": "si-ái",
            "Quality Gate": "cuóliti gueit",
        }
        source = "El CI ejecuta el Quality Gate."
        result = module.apply_edge_aliases(source, aliases)
        self.assertIn("si-ái", result)
        self.assertIn("cuóliti gueit", result)

    def test_azure_language_markup_uses_complete_terms_only(self):
        rendered = module.render_mixed_language_text(
            "La petición pasa por CI y termina.",
            english_terms=["CI"],
            aliases={},
            english_locale="en-GB",
        )
        self.assertIn("petición", rendered)
        self.assertNotIn("peti<lang", rendered)
        self.assertIn('<lang xml:lang="en-GB">CI</lang>', rendered)


if __name__ == "__main__":
    unittest.main()
