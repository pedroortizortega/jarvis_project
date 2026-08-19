import unittest

from knowledge_vault.note import (
    MissingType,
    body_of,
    parse_frontmatter,
    render,
    title_of,
)

NOTE = """---
type: infra-fact
title: Longhorn no esta instalado
aliases: [Longhorn no esta instalado]
tags: [storage, k3s]
timestamp: 2026-08-04T22:30:00Z
---
# Longhorn no esta instalado

El unico storage class es local-path.
"""


class FrontmatterTests(unittest.TestCase):
    def test_it_reads_scalars_and_lists(self):
        fields = parse_frontmatter(NOTE)
        self.assertEqual("infra-fact", fields["type"])
        self.assertEqual(["storage", "k3s"], fields["tags"])
        self.assertEqual(["Longhorn no esta instalado"], fields["aliases"])

    def test_the_body_excludes_the_frontmatter(self):
        body = body_of(NOTE)
        self.assertTrue(body.startswith("# Longhorn"))
        self.assertNotIn("type:", body)

    def test_a_note_without_frontmatter_is_all_body(self):
        self.assertEqual({}, parse_frontmatter("# Suelta\nTexto."))
        self.assertEqual("# Suelta\nTexto.", body_of("# Suelta\nTexto."))

    def test_the_title_comes_from_the_first_heading(self):
        self.assertEqual("Longhorn no esta instalado", title_of(NOTE))
        self.assertEqual("Suelta", title_of("# Suelta\nTexto."))
        self.assertIsNone(title_of("Sin encabezado."))

    def test_okf_requires_a_type(self):
        with self.assertRaises(MissingType):
            render("# Sin tipo\nTexto.", {"title": "Sin tipo"}, note_id="20260804223000")

    def test_rendering_round_trips_through_parsing(self):
        rendered = render(
            "# Titulo\nCuerpo.",
            {"type": "decision", "tags": ["a", "b"], "aliases": ["Titulo"]},
            note_id="20260804223000",
        )
        fields = parse_frontmatter(rendered)
        self.assertEqual("decision", fields["type"])
        self.assertEqual("20260804223000", fields["id"])
        self.assertEqual(["a", "b"], fields["tags"])
        self.assertEqual("# Titulo\nCuerpo.", body_of(rendered).strip())

    def test_a_value_with_a_colon_survives(self):
        rendered = render(
            "# Storage: solo local-path\nCuerpo.",
            {"type": "infra-fact"},
            note_id="20260804223000",
        )
        self.assertEqual("Storage: solo local-path", parse_frontmatter(rendered)["title"])


if __name__ == "__main__":
    unittest.main()
