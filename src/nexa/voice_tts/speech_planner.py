"""M2.4B.2 — NeXa-owned speech planner: *what text* reaches Piper.

A single small ``FrameProcessor`` placed between ``AssistantSpeechBridge``
and Pipecat's ``PiperHttpTTSService``:

    AssistantSpeechBridge → NexaSpeechPlanner → PiperHttpTTSService

It consumes the streamed assistant-text copy (``LLMTextFrame``) and emits
speech-oriented ``AggregatedTextFrame`` batches — one natural phrase at a
time — which ``PiperHttpTTSService.process_frame`` synthesizes directly,
bypassing its English-only ``SimpleTextAggregator`` (verified in R0012).

Scope for B.2 (see ``docs/reports/R0015_…``): better phrase boundaries +
TTS-only Markdown/list normalization. **No pacing, no audio-buffer
control, no CPU scheduling, no look-ahead controller** — that is B.3.

Invariants this module must never break:

* It only ever sees the already-emitted assistant-token copy. It never
  imports or constructs a model provider / ``ConversationSession`` /
  persona, and never mutates canonical assistant text or history — the
  transcript ``ConversationSession`` stores is byte-identical with or
  without this planner.
* It is **not** a response-language classifier. It reads the language that
  ``AssistantSpeechBridge`` already decided (from the ``TTSUpdateSettingsFrame``
  voice it forwards) purely to pick a list connector word ("oraz" / "and")
  and the abbreviation-expansion locale. The canonical decision stays in
  ``nexa.conversation.language`` via the bridge.
* Every frame that is not an ``LLMTextFrame`` is forwarded unchanged and
  in order. ``LLMTextFrame`` is consumed and replaced by zero or more
  ``AggregatedTextFrame``.
* Normalization never raises — a failure falls back to a minimal marker
  strip so a turn can never be silenced by this planner.
"""

from __future__ import annotations

import re

from loguru import logger
from pipecat.frames.frames import (
    AggregatedTextFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.text.base_text_aggregator import AggregationType

# --------------------------------------------------------------------------- #
# Tunables — CANDIDATE values (R0012 §"SEMANTIC CHUNKING"). Not product truth;
# constructor-overridable; to be A/B-tuned with the B.1 --report profiler.
# --------------------------------------------------------------------------- #

#: Minimum alphanumeric length for a *sentence* (``. ! ? …``) boundary to be
#: emitted on its own — big enough to swallow "1." / "np." junk, small
#: enough that a real short sentence ("Czarna dziura to obszar.") still
#: stands. Below it the boundary is ignored and the text merges forward.
MIN_SENTENCE_CHARS = 12
#: Minimum alphanumeric length of the left side for a ``;`` / ``:`` boundary
#: — so "Czy chodzi Ci o:" (12) is never spoken alone but a real lead-in
#: clause is.
MIN_CLAUSE_CHARS = 18
#: Minimum alphanumeric length of the left side for a *comma* to be treated
#: as a phrase boundary at all (commas are otherwise not split on).
MIN_COMMA_CHARS = 40
#: A comma boundary is only taken once the un-emitted buffer has grown at
#: least this long with no sentence/semicolon/colon boundary in sight.
COMMA_MIN_BUFFER_CHARS = 120
#: Hard cap — if the un-emitted buffer passes this with no boundary at all,
#: cut at the last space so text is never held indefinitely.
MAX_PHRASE_CHARS = 240

#: Back-compat alias (kept for callers/tests that referenced the pre-split name).
MIN_PHRASE_CHARS = MIN_SENTENCE_CHARS

_SENTENCE_ENDERS = ".!?…"
_CLAUSE_ENDERS = ";:"

# Polish abbreviations that must NOT end a sentence. Stored without the
# trailing period; a candidate token (optionally with one internal dot, for
# "m.in") is matched case-insensitively.
_PL_ABBREV: frozenset[str] = frozenset(
    {
        "tzw", "np", "itd", "itp", "m.in", "ul", "al", "dr", "prof", "inż",
        "mgr", "św", "nr", "str", "godz", "min", "sek", "tys", "mln", "mld",
        "ok", "tj", "cd", "dot", "par", "przyp", "ww", "pt", "ds",
    }
)
_EN_ABBREV: frozenset[str] = frozenset(
    {
        "e.g", "i.e", "mr", "mrs", "ms", "dr", "prof", "vs", "etc", "st",
        "no", "fig", "inc", "ltd", "jr", "sr", "approx", "dept", "est",
    }
)
_ALL_ABBREV = _PL_ABBREV | _EN_ABBREV

# Conservative, non-gendered, meaning-preserving TTS-only expansions
# (R0012). "tzw." is deliberately NOT expanded — its expansion is
# gender-sensitive ("tak zwany/zwana/zwane") and cannot be chosen safely
# from the abbreviation alone, so it is left attached to the following
# phrase instead (the warning in the B.2 brief).
#
# Each entry is (abbreviation-with-dot, spoken-form). The abbreviation's
# trailing period is always dropped — an expanded abbreviation must never
# fabricate a mid-sentence break ("planety itd. w galaktyce"). A genuinely
# sentence-final abbreviation ("A, B, C itd.") then loses its full stop and
# merges with the next sentence: acceptable prosody, and far better than a
# false split.
_PL_EXPANSIONS: tuple[tuple[str, str], ...] = (
    ("m.in.", "między innymi"),
    ("np.", "na przykład"),
    ("tj.", "to jest"),
    ("itd.", "i tak dalej"),
    ("itp.", "i tym podobne"),
)
_EN_EXPANSIONS: tuple[tuple[str, str], ...] = (
    ("e.g.", "for example"),
    ("i.e.", "that is"),
    ("etc.", "and so on"),
)

_ITEM_RE = re.compile(r"^[ \t]*(?:\d+[.)]|[-*+•·‣▪])[ \t]+(.+?)[ \t]*$")
_LEADING_MARKER_RE = re.compile(r"^[ \t]*(?:\d+[.)]|[-*+•·‣▪])[ \t]+")
#: a line that is only a list marker (or a marker with no content yet) — the
#: half-arrived tail of a streamed list, never a real run terminator.
_BARE_MARKER_RE = re.compile(r"^[ \t]*(?:\d+[.)]?|[-*+•·‣▪])[ \t]*$")

#: A list is spoken with an "oraz"/"and" connector only when every item is
#: this short (a list of names — "Call of Duty oraz Battlefield"). Longer,
#: multi-sentence items are joined as plain consecutive sentences instead
#: (markers dropped, each item keeps its own full stop) — B.2A: "oraz"
#: between paragraphs read badly on the real operator run.
_LIST_PROSE_MAX_ITEM_CHARS = 60

#: A trailing run of these — a dangling opener, a bare dash — that the
#: source left at the end of a phrase (e.g. a reply truncated by
#: ``num_predict`` right after "(") carries no speech content and is
#: trimmed from the spoken copy (never from the transcript). Closing
#: brackets and ``. ! ? … , ; :`` are kept.
_DANGLING_TAIL = " \t([{„«‹<–—-"

# LaTeX / Markdown-math wrappers (B.2A). gemma4:e4b emits inline math such
# as ``$\text{H}$`` for element symbols inside bolded list titles.
_MATH_SPAN_RES = (
    re.compile(r"\$\$(.+?)\$\$", re.DOTALL),
    re.compile(r"\\\[(.+?)\\\]", re.DOTALL),
    re.compile(r"\\\((.+?)\\\)", re.DOTALL),
    re.compile(r"\$(.+?)\$", re.DOTALL),
)
_MATH_TEXT_CMD_RE = re.compile(
    r"\\(?:text|mathrm|mathbf|mathit|mathsf|mathtt|mathcal|operatorname|boldsymbol|"
    r"textbf|textit|textrm|rm|bf|it)\s*\{([^{}]*)\}"
)


def _alnum_len(s: str) -> int:
    return sum(1 for ch in s if ch.isalnum())


def _norm_language(language: str | None) -> str:
    return "pl" if language == "pl" else "en"


# --------------------------------------------------------------------------- #
# TTS-only normalization (pure, deterministic, never raises)
# --------------------------------------------------------------------------- #


def _minimal_strip(text: str) -> str:
    """Last-resort fallback: drop the obvious formatting characters and
    collapse whitespace. Used only if the full normalizer raises."""
    text = re.sub(r"[*_`#>|]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _expand_abbreviations(text: str, language: str) -> str:
    pairs = _PL_EXPANSIONS if language == "pl" else _EN_EXPANSIONS
    for abbr, full in pairs:
        # word-start boundary, followed by whitespace or end — never inside a
        # larger token, never touching a following word. re.escape keeps the
        # abbreviation's own dot literal; it is not carried into the output.
        pat = re.compile(r"(?<![\w.])" + re.escape(abbr) + r"(?=\s|$)", re.IGNORECASE)
        text = pat.sub(full, text)
    return text


def _strip_emphasis(text: str) -> str:
    text = text.replace("**", "").replace("__", "")
    # paired single * or _ around a non-space span on one line
    text = re.sub(r"(?<!\w)([*_])(\S(?:[^\n*_]*\S)?)\1(?!\w)", r"\2", text)
    return text


def _despan_math(inner: str) -> str:
    """Reduce the contents of a math span to readable text. Never interprets
    an equation — just removes the markup."""
    s = _MATH_TEXT_CMD_RE.sub(r"\1", inner)
    s = re.sub(r"\\[a-zA-Z]+\s*\{([^{}]*)\}", r"\1", s)  # \frac{a}{b}-ish -> keep args
    s = re.sub(r"\\[a-zA-Z]+", "", s)  # bare \alpha, \cdot, \left ... -> drop
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"[_^]", "", s)  # sub/superscript markers
    s = re.sub(r"\\[^a-zA-Z\s]", "", s)  # \, \! \; etc.
    return re.sub(r"\s{2,}", " ", s).strip()


def _strip_math(text: str) -> str:
    for pat in _MATH_SPAN_RES:
        text = pat.sub(lambda m: _despan_math(m.group(1)), text)
    # bare \text{...} outside any span
    text = _MATH_TEXT_CMD_RE.sub(r"\1", text)
    # any leftover unmatched delimiters
    text = text.replace("$", "")
    text = text.replace(r"\(", "").replace(r"\)", "").replace(r"\[", "").replace(r"\]", "")
    return text


def _tidy_spoken(text: str) -> str:
    """Final polish on a phrase's *spoken* copy (never the transcript):
    drop a dangling opener / bare dash the source left at the tail, and an
    unbalanced trailing quote. Keeps closing brackets and ``. ! ? … , ; :``.
    """
    text = text.strip()
    stripped = text.rstrip(_DANGLING_TAIL)
    if stripped != text:
        text = stripped.rstrip()
    if text.count('"') % 2 == 1 and text.endswith('"'):
        text = text[:-1].rstrip()
    return text


def _join_items(items: list[str], language: str) -> str:
    parts = [i.strip() for i in items if i.strip()]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) >= 2 and all(len(p) <= _LIST_PROSE_MAX_ITEM_CHARS for p in parts):
        # short items = a list of names -> natural "a, b oraz c."
        names = [p.rstrip(" ,;:.!?…") for p in parts]
        conn = " oraz " if language == "pl" else " and "
        if len(names) == 2:
            body = names[0] + conn + names[1]
        else:
            body = ", ".join(names[:-1]) + conn + names[-1]
        return body + "."
    # long / multi-sentence items -> just drop the markers, keep each as its
    # own sentence(s) so "oraz" never joins two paragraphs.
    out = []
    for p in parts:
        out.append(p if p[-1] in ".!?…:" else p + ".")
    return " ".join(out)


def _normalize_lists(text: str, language: str, *, streaming: bool) -> str:
    """Collapse runs of consecutive Markdown list items into natural prose.

    A trailing run that still touches the end of a streamed buffer is held
    (dropped from this pass' output) until a non-item line terminates it or
    the stream ends — so list content is never spoken half-formed and never
    re-spoken.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if _ITEM_RE.match(lines[i]):
            j = i
            while j < n and _ITEM_RE.match(lines[j]):
                j += 1
            if streaming:
                # the run is "done" only once a real non-item line follows it
                # (not end-of-buffer, not a blank line, not a half-typed
                # marker) — otherwise more items may still be streaming in.
                terminated = any(
                    ln.strip() and not _BARE_MARKER_RE.match(ln) and not _ITEM_RE.match(ln)
                    for ln in lines[j:]
                )
                if not terminated:
                    return "\n".join(out).rstrip()
            items = [_LEADING_MARKER_RE.sub("", lines[k]).strip() for k in range(i, j)]
            joined = _join_items(items, language)
            if joined:
                out.append(joined)
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _normalize(text: str, language: str, *, streaming: bool, expand: bool) -> str:
    # fenced code — never read code aloud
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = text.replace("```", " ")
    # inline code
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = text.replace("`", "")
    # LaTeX / Markdown math ($...$, \(...\), \[...\], bare \text{...})
    text = _strip_math(text)
    # images / links / autolinks / bare urls
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = re.sub(r"https?://(\S+)", lambda m: m.group(1).rstrip("/.,)"), text)
    # headings + blockquotes (line-anchored, before list handling)
    text = re.sub(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+", "", text)
    text = re.sub(r"(?m)[ \t]+#+[ \t]*$", "", text)
    text = re.sub(r"(?m)^[ \t]{0,3}>[ \t]?", "", text)
    # emphasis
    text = _strip_emphasis(text)
    # tables → drop the pipe/rule scaffolding, keep cell text
    text = re.sub(
        r"(?m)^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$", "", text
    )
    text = text.replace("|", " ")
    # list runs → prose
    text = _normalize_lists(text, language, streaming=streaming)
    # abbreviation expansion (conservative, TTS-only)
    if expand:
        text = _expand_abbreviations(text, language)
    # whitespace: newlines become spaces, runs collapse
    text = re.sub(r"[ \t]*\n[ \t]*", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    # leftover lone markers
    text = re.sub(r"(?<=\s)[*_#•·‣▪>](?=\s)", " ", text)
    text = re.sub(r"^[\s*_#>•·‣▪-]+", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    # tidy space before punctuation
    text = re.sub(r"\s+([,.;:!?…])", r"\1", text)
    return text.strip()


def normalize_for_speech(
    text: str, *, language: str = "en", streaming: bool = False, expand_abbreviations: bool = True
) -> str:
    """Return a TTS-only cleaned copy of ``text``.

    ``streaming=True`` holds a list run that still touches the end of the
    buffer (it will be released once terminated). Never raises: on any
    internal error it falls back to :func:`_minimal_strip`.
    """
    if not text or not text.strip():
        return ""
    lang = _norm_language(language)
    try:
        return _normalize(text, lang, streaming=streaming, expand=expand_abbreviations)
    except Exception:  # pragma: no cover - defensive; must never silence a turn
        logger.exception("nexa.voice_tts speech planner: normalize fell back to minimal strip")
        return _minimal_strip(text)


# --------------------------------------------------------------------------- #
# Polish/English-aware phrase segmentation (pure)
# --------------------------------------------------------------------------- #


def _ends_with_abbreviation(text_including_dot: str) -> bool:
    """``text_including_dot`` ends with ``.`` — is that period part of a
    known abbreviation (``tzw.``, ``m.in.``, ``e.g.``) or an initial
    (``J.``) rather than a sentence end?"""
    m = re.search(r"([A-Za-zÀ-ɏ](?:[A-Za-zÀ-ɏ.]*[A-Za-zÀ-ɏ])?)\.$", text_including_dot)
    if not m:
        return False
    token = m.group(1).lower()
    if token in _ALL_ABBREV:
        return True
    if "." in token:
        pieces = token.split(".")
        if pieces[-1] in _ALL_ABBREV or ".".join(pieces[-2:]) in _ALL_ABBREV:
            return True
    # a single letter + period = an initial, not a sentence end
    if len(token) == 1 and token.isalpha():
        return True
    return False


def find_phrase_cut(
    s: str,
    *,
    min_sentence: int = MIN_SENTENCE_CHARS,
    min_clause: int = MIN_CLAUSE_CHARS,
    min_comma: int = MIN_COMMA_CHARS,
    comma_min_buffer: int = COMMA_MIN_BUFFER_CHARS,
    max_phrase: int = MAX_PHRASE_CHARS,
) -> int | None:
    """Index at which to cut ``s`` into ``(phrase, remainder)``, or ``None``
    if no good boundary is available yet.

    A cut always leaves at least one non-space character after it
    (lookahead) — so a period at the very end of the current text waits for
    the next token, which also defers a decimal like ``3.5`` until the
    digit after the point has arrived.

    Boundary priority: sentence (``. ! ? …``) → ``;`` / ``:`` → a comma, but
    only once the left side is a substantial clause *and* the un-emitted
    buffer is already long. Never split inside ``(...)`` or a quoted span,
    never after a known abbreviation, never on a decimal point.
    """
    n = len(s)
    depth = 0
    in_dq = False
    best_comma: int | None = None
    i = 0
    while i < n:
        c = s[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        elif c == '"':
            in_dq = not in_dq
        elif c in "„«":
            in_dq = True
        elif c in "”»":
            in_dq = False
        inside = depth > 0 or in_dq

        if not inside and c in _SENTENCE_ENDERS:
            j = i
            while j < n and s[j] in _SENTENCE_ENDERS:
                j += 1
            run = s[i:j]
            if s[j:].strip() == "":
                break  # boundary at the tail — wait for lookahead / more text
            is_ellipsis = "…" in run or run.count(".") >= 2
            if not is_ellipsis and run[0] == ".":
                if i > 0 and s[i - 1].isdigit() and j < n and s[j].isdigit():
                    i = j
                    continue
                if _ends_with_abbreviation(s[: i + 1]):
                    i = j
                    continue
            if _alnum_len(s[:j]) >= min_sentence:
                return j
            i = j  # below the floor — merge forward
            continue

        if not inside and c in _CLAUSE_ENDERS:
            if s[i + 1 :].strip() == "":
                break
            if _alnum_len(s[: i + 1]) >= min_clause:
                return i + 1

        if not inside and c == "," and best_comma is None:
            if s[i + 1 :].strip() != "" and _alnum_len(s[: i + 1]) >= min_comma:
                best_comma = i + 1

        i += 1

    if best_comma is not None and len(s) >= comma_min_buffer:
        return best_comma
    if len(s) >= max_phrase:
        cut = s.rfind(" ", 0, max_phrase)
        if cut > min_sentence:
            return cut + 1
    return None


# --------------------------------------------------------------------------- #
# The frame processor
# --------------------------------------------------------------------------- #


class NexaSpeechPlanner(FrameProcessor):
    """Turn the streamed ``LLMTextFrame`` copy into speech-oriented
    ``AggregatedTextFrame`` phrases. See the module docstring for scope and
    invariants."""

    def __init__(
        self,
        *,
        en_voice: str,
        pl_voice: str,
        default_language: str = "en",
        min_sentence_chars: int = MIN_SENTENCE_CHARS,
        min_clause_chars: int = MIN_CLAUSE_CHARS,
        min_comma_chars: int = MIN_COMMA_CHARS,
        comma_min_buffer_chars: int = COMMA_MIN_BUFFER_CHARS,
        max_phrase_chars: int = MAX_PHRASE_CHARS,
        expand_abbreviations: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._en_voice = en_voice
        self._pl_voice = pl_voice
        self._lang = _norm_language(default_language)
        self._min_sentence = min_sentence_chars
        self._min_clause = min_clause_chars
        self._min_comma = min_comma_chars
        self._comma_min_buffer = comma_min_buffer_chars
        self._max_phrase = max_phrase_chars
        self._expand = expand_abbreviations
        self._raw = ""
        self._emitted = ""

    # -- lifecycle -------------------------------------------------------- #

    def _reset(self) -> None:
        self._raw = ""
        self._emitted = ""

    def _language_from_settings(self, frame: TTSUpdateSettingsFrame) -> str | None:
        voice = getattr(frame.delta, "voice", None)
        if voice is None and isinstance(getattr(frame, "settings", None), dict):
            voice = frame.settings.get("voice")
        if voice == self._pl_voice:
            return "pl"
        if voice == self._en_voice:
            return "en"
        return None

    # -- normalization + segmentation (NO pacing) ----------------------- #

    def _pending_phrases(self, *, final: bool) -> list[str]:
        norm = normalize_for_speech(
            self._raw,
            language=self._lang,
            streaming=not final,
            expand_abbreviations=self._expand,
        )
        if not norm:
            return []
        if norm.startswith(self._emitted):
            tail = norm[len(self._emitted) :]
        elif not self._emitted:
            tail = norm
        else:
            # non-monotone normalization (rare) — do not risk a double-speak
            return []

        phrases: list[str] = []
        if final:
            # ``_emitted`` tracking stays on the raw normalized text (prefix
            # stability); only the spoken copy is tidied. A partial final
            # word from an upstream ``num_predict`` truncation is kept as-is
            # — transcript truth, no invented completion — but a dangling
            # trailing "(" the model left is trimmed (un-speakable, no
            # content). See B.2A / R0016.
            self._emitted = norm
            spoken = _tidy_spoken(tail)
            if _alnum_len(spoken) >= 1:
                phrases.append(spoken)
            return phrases

        while True:
            lead = len(tail) - len(tail.lstrip())
            body = tail[lead:]
            idx = find_phrase_cut(
                body,
                min_sentence=self._min_sentence,
                min_clause=self._min_clause,
                min_comma=self._min_comma,
                comma_min_buffer=self._comma_min_buffer,
                max_phrase=self._max_phrase,
            )
            if idx is None:
                break
            phrase = _tidy_spoken(body[:idx])
            self._emitted += tail[: lead + idx]
            tail = tail[lead + idx :]
            if _alnum_len(phrase) >= 1:
                phrases.append(phrase)
        return phrases

    async def _emit(self, phrases: list[str], template: LLMTextFrame | None) -> None:
        for text in phrases:
            out = AggregatedTextFrame(
                text=text,
                aggregated_by=AggregationType.SENTENCE,
                raw_text=text,
            )
            out.append_to_context = True
            out.skip_tts = getattr(template, "skip_tts", None)
            await self.push_frame(out)

    # -- Pipecat entry point ------------------------------------------- #

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._reset()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSUpdateSettingsFrame):
            lang = self._language_from_settings(frame)
            if lang is not None:
                self._lang = lang
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame):
            self._raw += frame.text
            await self._emit(self._pending_phrases(final=False), frame)
            return  # consumed — never forwarded (TTS would double-process)

        if isinstance(frame, LLMFullResponseEndFrame):
            await self._emit(self._pending_phrases(final=True), None)
            self._reset()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            # M2.4 half-duplex only — no barge-in yet. Just clear partial
            # state so nothing leaks into the next reply.
            self._reset()
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
