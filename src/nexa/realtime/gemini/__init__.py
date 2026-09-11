"""The Gemini Live realtime-voice implementation (ADR-0004 Decision C).

M2.6B.1 ships only the pieces that need no cloud SDK: credential loading
(``credentials.py``) and the voice-preference mapping (``voice.py``). The
production ``GeminiLiveProvider`` (wrapping Pipecat's
``GeminiLiveLLMService``, ``pipecat-ai`` + the optional ``google-genai``
dependency) is M2.6B.2 — this package does not import it, so importing
``nexa.realtime.gemini`` today pulls in **no** Google cloud code.
"""

from __future__ import annotations
