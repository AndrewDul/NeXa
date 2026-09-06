"""M2.4 offline-safety preflight tests — proves NeXa never silently
downloads NLTK's `punkt_tab` data at runtime; it fails explicitly instead.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.tts.errors import SentenceTokenizerDataMissingError  # noqa: E402
from nexa.voice_tts import ensure_sentence_tokenizer_data  # noqa: E402


class TestEnsureSentenceTokenizerData(unittest.TestCase):
    def test_present_data_passes_silently(self) -> None:
        with mock.patch("nltk.data.find", return_value=None) as find:
            ensure_sentence_tokenizer_data()  # must not raise
        find.assert_called_once_with("tokenizers/punkt_tab")

    def test_missing_data_raises_explicitly_never_downloads(self) -> None:
        with (
            mock.patch("nltk.data.find", side_effect=LookupError("not found")),
            mock.patch("nltk.download") as download,
        ):
            with self.assertRaises(SentenceTokenizerDataMissingError):
                ensure_sentence_tokenizer_data()
            download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
