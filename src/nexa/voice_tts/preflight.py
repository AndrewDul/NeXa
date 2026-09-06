"""Offline-safety preflight for Pipecat's sentence aggregation (M2.4).

R0010 found Pipecat's `SimpleTextAggregator` uses NLTK's `sent_tokenize`,
which downloads its `punkt_tab` data from the network on first use if not
already cached (`nltk.download("punkt_tab", quiet=True)` inside
`pipecat.utils.string._sent_tokenizer()`). NeXa is local-first/offline-first
— the *product* runtime must never trigger that silently. The one,
deliberate, developer-triggered place this is ever downloaded is
`scripts/setup_piper_http.py`; this module only checks and fails
explicitly, it never downloads.
"""

from __future__ import annotations

from nexa.tts.errors import SentenceTokenizerDataMissingError


def ensure_sentence_tokenizer_data() -> None:
    """Raise `SentenceTokenizerDataMissingError` if NLTK's `punkt_tab`
    sentence-tokenizer data is not already present — never downloads it.

    Call this before starting a pipeline that uses Pipecat's SENTENCE text
    aggregation mode (the M2.4 default), so a missing offline asset fails
    fast with a clear fix, instead of Pipecat silently reaching the network
    the first time a sentence needs to be split.
    """
    import nltk

    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError as exc:
        raise SentenceTokenizerDataMissingError(
            "NLTK 'punkt_tab' sentence-tokenizer data is not installed. "
            "NeXa never downloads this silently at runtime. "
            "Run: python3 scripts/setup_piper_http.py"
        ) from exc
