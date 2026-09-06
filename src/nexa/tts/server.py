"""``PiperHttpServer`` — owns the external Piper HTTP server process
lifecycle (M2.4, ADR-0003 D6).

The external process runs from its own virtual environment
(`nexa.tts.config.piper_venv_dir()`), never NeXa's own `.venv` — this is
the technical boundary that keeps the GPL-3.0-licensed `piper-tts` package
out of NeXa's own running process (R0010). This module owns: starting the
subprocess, waiting for it to become ready, prewarming both configured
voices, synthesizing (a thin `aiohttp` client of the real, verified
`/synthesize` endpoint — R0010 found `POST /` returns 405), and stopping
the process cleanly. No Pipecat dependency here — that lives in
`nexa.voice_tts`, which uses this class's `config` to construct Pipecat's
`PiperHttpTTSService`.
"""

from __future__ import annotations

import asyncio
import time

import aiohttp
from loguru import logger

from .config import PiperHttpConfig
from .errors import (
    PiperHttpError,
    PiperServerStartError,
    PiperVenvNotFoundError,
    PiperVoiceNotFoundError,
)


class PiperHttpServer:
    """Starts, prewarms, and stops the external `python -m piper.http_server`
    process. One process serves both the configured PL and EN voices
    (R0010: a single process can dynamically load additional voices on
    request, ~2.4s first load, ~0.2s after — `prewarm()` pays that cost
    once at startup instead of on a real user's first response)."""

    def __init__(self, config: PiperHttpConfig | None = None) -> None:
        self.config = config or PiperHttpConfig()
        if not self.config.venv_python.is_file():
            raise PiperVenvNotFoundError(
                f"Piper external venv not found at {self.config.venv_python}. "
                f"Run: python3 scripts/setup_piper_http.py"
            )
        default_model = self.config.voices_dir / f"{self.config.en_voice}.onnx"
        if not default_model.is_file():
            raise PiperVoiceNotFoundError(
                f"Piper voice model not found at {default_model}. "
                f"Run: python3 scripts/setup_piper_http.py"
            )
        self._process: asyncio.subprocess.Process | None = None
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """Launch the external Piper HTTP server subprocess and wait until
        it responds to requests. Raises `PiperServerStartError` if it never
        becomes ready within `config.startup_timeout_s`."""
        if self._process is not None:
            return  # idempotent

        self._session = aiohttp.ClientSession()
        self._process = await asyncio.create_subprocess_exec(
            str(self.config.venv_python),
            "-m", "piper.http_server",
            "-m", self.config.en_voice,
            "--data-dir", str(self.config.voices_dir),
            "--host", self.config.host,
            "--port", str(self.config.port),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        deadline = time.monotonic() + self.config.startup_timeout_s
        while time.monotonic() < deadline:
            if self._process.returncode is not None:
                output = await self._process.stdout.read()
                raise PiperServerStartError(
                    f"Piper HTTP server exited early (code {self._process.returncode}): "
                    f"{output.decode(errors='replace')[-2000:]}"
                )
            try:
                async with self._session.get(
                    self.config.info_url, timeout=aiohttp.ClientTimeout(total=1.0)
                ) as resp:
                    if resp.status == 200:
                        logger.info(f"nexa.tts: Piper HTTP server ready at {self.config.base_url}")
                        return
            except (TimeoutError, aiohttp.ClientError):
                pass
            await asyncio.sleep(0.2)

        await self.stop()
        raise PiperServerStartError(
            f"Piper HTTP server did not become ready within "
            f"{self.config.startup_timeout_s}s at {self.config.base_url}"
        )

    async def prewarm(self) -> dict[str, float]:
        """Force both configured voices to load before real use, so the
        first real user response never pays the ~2.4s cross-voice load
        penalty (R0010). Returns elapsed seconds per voice."""
        elapsed: dict[str, float] = {}
        for voice in (self.config.en_voice, self.config.pl_voice):
            t0 = time.monotonic()
            await self.synthesize("warmup.", voice=voice)
            elapsed[voice] = time.monotonic() - t0
            logger.info(f"nexa.tts: prewarmed voice {voice!r} in {elapsed[voice]:.2f}s")
        return elapsed

    async def synthesize(self, text: str, *, voice: str) -> bytes:
        """One-off synthesis request, used by `prewarm()` and available for
        direct testing/health-checks. The real product audio path goes
        through Pipecat's `PiperHttpTTSService`, not this method."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        try:
            async with self._session.post(
                self.config.synthesize_url,
                json={"text": text, "voice": voice},
                timeout=aiohttp.ClientTimeout(total=self.config.request_timeout_s),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise PiperHttpError(f"Piper HTTP server returned {resp.status}: {body}")
                return await resp.read()
        except aiohttp.ClientError as exc:
            raise PiperHttpError(f"Piper HTTP request failed: {exc}") from exc

    async def stop(self) -> None:
        """Terminate the subprocess cleanly. Idempotent."""
        if self._session is not None:
            await self._session.close()
            self._session = None
        if self._process is not None:
            if self._process.returncode is None:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            self._process = None
