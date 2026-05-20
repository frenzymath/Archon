"""Tests for sync_leanok's tautological-placeholder detection.

When a decl's body matches one of the known trivial-placeholder
patterns (``⟨Iso.refl _⟩``, top-level ``Classical.choice``, etc.),
``sync_leanok`` must abstain from adding ``\\leanok`` to the
corresponding proof block — even though the decl is kernel-clean
and contains no ``sorry``. Otherwise the deterministic marker
falsely advertises the substantive proof the blueprint prose
claims, and downstream consumers may use it believing the witness
is what the prose says.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


_SYNC = Path(__file__).resolve().parent.parent / (
    "src/archon/.archon-src/skills/lean4/lib/scripts/sync_leanok.py"
)


def _load_sync_module():
    # Register the module in sys.modules BEFORE exec_module — the
    # @dataclass decorator inside sync_leanok looks up its host module
    # via ``sys.modules[cls.__module__]``; without the registration we
    # get AttributeError on ``NoneType.__dict__`` (3.10 dataclass quirk).
    import sys
    name = "sync_leanok_module"
    spec = importlib.util.spec_from_file_location(name, _SYNC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


class DeclBodyIsTrivialPlaceholderTest(unittest.TestCase):
    """Direct tests of the trivial-body detector against real Lean
    source patterns the audits have flagged."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_sync_module()

    def _decl_file(self, body: str) -> Path:
        """Write a temp file containing one declaration with the given
        post-``:=`` body. Returns the file path."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".lean", delete=False, encoding="utf-8",
        )
        tmp.write(body)
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return Path(tmp.name)

    def test_iso_refl_in_brackets_is_trivial(self):
        body = "theorem foo : Nonempty (X ≅ X) := ⟨Iso.refl _⟩\n"
        path = self._decl_file(body)
        self.assertTrue(self.mod._decl_body_is_trivial_placeholder(path, "foo"))

    def test_classical_choice_is_trivial(self):
        body = (
            "noncomputable def foo : X := Classical.choice ⟨witness⟩\n"
        )
        path = self._decl_file(body)
        self.assertTrue(self.mod._decl_body_is_trivial_placeholder(path, "foo"))

    def test_classical_choice_with_explicit_alpha(self):
        body = (
            "noncomputable def foo : X :=\n"
            "  Classical.choice (α := X) ⟨witness⟩\n"
        )
        path = self._decl_file(body)
        self.assertTrue(self.mod._decl_body_is_trivial_placeholder(path, "foo"))

    def test_nonempty_intro_iso_refl_is_trivial(self):
        body = (
            "theorem foo : Nonempty (X ≅ X) :=\n"
            "  Nonempty.intro Iso.refl _\n"
        )
        path = self._decl_file(body)
        self.assertTrue(self.mod._decl_body_is_trivial_placeholder(path, "foo"))

    def test_substantive_body_is_not_trivial(self):
        body = (
            "theorem foo (h : X ≃ Y) : Nonempty (X ≅ Y) := by\n"
            "  exact ⟨h.toIso⟩\n"
        )
        path = self._decl_file(body)
        self.assertFalse(self.mod._decl_body_is_trivial_placeholder(path, "foo"))

    def test_tactic_proof_with_rfl_is_not_trivial(self):
        # ``by rfl`` on a substantive statement (e.g. defeq lemma) is a
        # legitimate proof, not a structural tautology. Only the
        # specific weakened patterns are caught.
        body = "theorem foo : 1 + 1 = 2 := by rfl\n"
        path = self._decl_file(body)
        self.assertFalse(self.mod._decl_body_is_trivial_placeholder(path, "foo"))

    def test_decl_not_found_returns_false(self):
        body = "theorem foo : True := trivial\n"
        path = self._decl_file(body)
        self.assertFalse(
            self.mod._decl_body_is_trivial_placeholder(path, "nonexistent"),
        )


class DeclHasSorryAttributionTest(unittest.TestCase):
    """``_decl_has_sorry`` must not answer a confident "no sorry" when the
    analyzer found a sorry it couldn't attribute to a declaration — that
    is exactly the gap that let a sorry-bearing proof keep its
    proof-block ``\\leanok``. It should return None (undecided), which
    the proof-block branch now treats as fail-safe removal.
    """

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_sync_module()

    def _patch_analyzer(self, sorries):
        import json as _json
        import types

        def fake_run(*_a, **_k):
            return types.SimpleNamespace(
                returncode=0, stdout=_json.dumps({"sorries": sorries}), stderr="",
            )

        orig = self.mod.subprocess.run
        self.mod.subprocess.run = fake_run
        self.addCleanup(lambda: setattr(self.mod.subprocess, "run", orig))

    def test_attributed_sorry_is_true(self):
        self._patch_analyzer([{"in_declaration": "foo"}])
        self.assertIs(self.mod._decl_has_sorry(Path("X.lean"), "foo"), True)

    def test_clean_file_is_false(self):
        self._patch_analyzer([])
        self.assertIs(self.mod._decl_has_sorry(Path("X.lean"), "foo"), False)

    def test_sorry_only_in_other_decl_is_false(self):
        self._patch_analyzer([{"in_declaration": "bar"}])
        self.assertIs(self.mod._decl_has_sorry(Path("X.lean"), "foo"), False)

    def test_unattributed_sorry_is_undecided(self):
        # A sorry the analyzer couldn't pin to a decl → can't certify
        # `foo` is clean → None (caller fail-safe removes the \leanok).
        self._patch_analyzer([{"in_declaration": None}])
        self.assertIsNone(self.mod._decl_has_sorry(Path("X.lean"), "foo"))


if __name__ == "__main__":
    unittest.main()
