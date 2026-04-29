"""
Mauricio Voice Satellite — stateless audio client for Raspberry Pi.

Connects:
  - openWakeWord (wyoming) on localhost:10400
  - faster-whisper (wyoming) on SERVER:10300
  - Piper TTS (wyoming) on SERVER:10200
  - Backend (HTTP) on SERVER:8000

Pipeline:
  detect wake → record with VAD → STT → POST /v1/voice/turn → TTS → play
  In voice_chat mode: skips wake word and listens again after each response.
"""

import asyncio
import logging
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

import httpx
import numpy as np
import sounddevice as sd
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize
from wyoming.wake import Detection

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname).1s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("satellite")


def _play(path: str) -> None:
    if sys.platform == "darwin":
        os.system(f"afplay {path}")
    elif sys.platform.startswith("linux"):
        # pw-play routes through PipeWire (respects default sink, e.g. BT speaker).
        # Falls back to aplay if pw-play isn't available.
        if os.system(f"command -v pw-play >/dev/null 2>&1") == 0:
            os.system(f"pw-play {path} 2>/dev/null")
        else:
            os.system(f"aplay -q {path}")
    else:
        print(f"[tts] unsupported platform {sys.platform}, audio at {path}")


SATELLITE_ID = os.getenv("SATELLITE_ID", "living-room")
SERVER = os.getenv("SERVER_HOST", "192.168.1.100")
BACKEND_URL = os.getenv("BACKEND_URL", f"http://{SERVER}:8000")
BACKEND_KEY = os.getenv("BACKEND_API_KEY")

WAKE_HOST = os.getenv("WAKE_HOST", "localhost")
WAKE_PORT = int(os.getenv("WAKE_PORT", "10400"))
STT_HOST = os.getenv("STT_HOST", SERVER)
STT_PORT = int(os.getenv("STT_PORT", "10300"))
TTS_HOST = os.getenv("TTS_HOST", SERVER)
TTS_PORT = int(os.getenv("TTS_PORT", "10200"))

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"

# Set AUDIO_DEVICE to an int index or ALSA device name (e.g. "hw:2,0")
# Leave unset to use the system default.
_dev_env = os.getenv("AUDIO_DEVICE")
AUDIO_DEVICE: int | str | None = (
    int(_dev_env) if _dev_env and _dev_env.lstrip("-").isdigit() else _dev_env or None
)

VAD_SILENCE_MS = 800  # ms of silence to stop recording
MAX_RECORD_S = 12  # hard cutoff once speech started
PRE_SPEECH_TIMEOUT_S = 3  # abort if no speech detected within this window
RMS_SILENCE_THRESHOLD = 350  # tune empirically with your mic


_WAKE_WORD = os.getenv("WAKE_WORD", "alexa")
_WAKE_THRESHOLD = float(os.getenv("WAKE_THRESHOLD", "0.55"))
_OWW_FRAME = 1280  # 80 ms @ 16 kHz — must match model frame exactly
_WAKE_WARMUP_MS = 2000  # discard first ms after opening mic (drain BT/TTS echo tail)
_WAKE_CONSECUTIVE = 2  # frames in a row above threshold needed to fire (kills echo flicks)
_PREROLL_MS = 700  # of audio kept before wake fires, prepended to recording
_DING_ENABLED = os.getenv("DING_ENABLED", "0") == "1"  # off: BT latency makes it useless
_oww_model = None


def _get_oww():
    global _oww_model
    if _oww_model is None:
        from openwakeword.model import Model
        _oww_model = Model()
    return _oww_model


async def detect_wake_word() -> bytes:
    """Blocks until wake word is detected. Returns the last ~_PREROLL_MS of mic
    audio so the recorder can prepend it (avoids cutting off the user's speech
    that came right after 'alexa')."""
    log.info("wake listening word=%s threshold=%.2f", _WAKE_WORD, _WAKE_THRESHOLD)
    model = _get_oww()
    model.reset()
    detected = asyncio.Event()
    loop = asyncio.get_event_loop()
    q: asyncio.Queue = asyncio.Queue()
    buf = np.zeros(0, dtype=np.int16)
    preroll_samples = int(SAMPLE_RATE * _PREROLL_MS / 1000)
    preroll = np.zeros(0, dtype=np.int16)

    def callback(indata, frames, time_info, status):
        loop.call_soon_threadsafe(q.put_nowait, indata.copy())

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        callback=callback,
        blocksize=_OWW_FRAME,
        device=AUDIO_DEVICE,
    ):
        warmup_samples = int(SAMPLE_RATE * _WAKE_WARMUP_MS / 1000)
        warmed = 0
        consecutive_hits = 0
        while not detected.is_set():
            chunk = await q.get()
            samples = chunk.flatten()
            # always feed preroll, including during warmup, so we always have
            # the immediately-prior audio to prepend
            preroll = np.concatenate([preroll, samples])[-preroll_samples:]
            if warmed < warmup_samples:
                warmed += len(samples)
                continue
            buf = np.concatenate([buf, samples])
            while len(buf) >= _OWW_FRAME and not detected.is_set():
                frame = buf[:_OWW_FRAME]
                buf = buf[_OWW_FRAME:]
                scores = model.predict(frame)
                score = float(scores.get(_WAKE_WORD, 0.0))
                if score >= _WAKE_THRESHOLD:
                    consecutive_hits += 1
                    if consecutive_hits >= _WAKE_CONSECUTIVE:
                        log.info("wake fired word=%s score=%.3f", _WAKE_WORD, score)
                        detected.set()
                        model.reset()
                else:
                    consecutive_hits = 0
    return preroll.tobytes()


def record_until_silence() -> bytes:
    """Records until prolonged silence or cap. Returns PCM 16-bit."""
    log.info("record listening")
    chunks = []
    silence_ms = 0
    total_ms = 0
    chunk_ms = 30
    chunk_samples = int(SAMPLE_RATE * chunk_ms / 1000)

    max_rms = 0
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        blocksize=chunk_samples,
        device=AUDIO_DEVICE,
    ) as stream:
        speech_started = False
        while True:
            data, _ = stream.read(chunk_samples)
            samples = data.flatten().astype(np.int32)
            rms = int(np.sqrt(np.mean(samples * samples))) if len(samples) else 0
            if rms > max_rms:
                max_rms = rms

            if rms > RMS_SILENCE_THRESHOLD:
                silence_ms = 0
                speech_started = True
            else:
                silence_ms += chunk_ms

            if speech_started:
                chunks.append(data.tobytes())

            total_ms += chunk_ms
            if speech_started and silence_ms >= VAD_SILENCE_MS:
                break
            if not speech_started and total_ms >= PRE_SPEECH_TIMEOUT_S * 1000:
                break  # nothing was said — abort fast (likely false wake)
            if total_ms >= MAX_RECORD_S * 1000:
                break

    log.info("record done chunks=%d max_rms=%d threshold=%d",
             len(chunks), max_rms, RMS_SILENCE_THRESHOLD)
    return b"".join(chunks)


DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "").strip()
DEEPGRAM_MODEL = os.getenv("DEEPGRAM_MODEL", "nova-2")


def _wrap_wav(pcm: bytes, rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> bytes:
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _peak_normalize(pcm: bytes, target_peak_db: float = -3.0, max_gain: float = 25.0) -> bytes:
    """Boost PCM so peak hits target_peak_db (avoids Deepgram returning empty for
    quiet recordings). Skipped for empty/silent buffers; capped to max_gain so
    pure noise floors don't get amplified into hiss."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.int32)
    if len(samples) == 0:
        return pcm
    peak = int(np.max(np.abs(samples)))
    if peak < 50:  # essentially silence
        return pcm
    target = int(32767 * (10 ** (target_peak_db / 20)))
    gain = min(target / peak, max_gain)
    if gain <= 1.05:  # already loud enough
        return pcm
    boosted = np.clip(samples * gain, -32767, 32767).astype(np.int16)
    log.info("audio boost peak=%d gain=%.2fx", peak, gain)
    return boosted.tobytes()


_DG_KEYWORDS = [
    "alexa:2",
    "Mauricio:2",
    "apaga:3", "apagar:3", "apágala:3",
    "enciende:3", "encender:3",
    "lámpara:2", "luz:2",
    "qué hora es:2",
]


async def _transcribe_deepgram(pcm: bytes) -> str:
    params = [
        ("language", "es"),
        ("model", DEEPGRAM_MODEL),
        ("smart_format", "true"),
        ("punctuate", "true"),
        *(("keywords", k) for k in _DG_KEYWORDS),
    ]
    headers = {
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
        "Content-Type": "audio/wav",
    }
    body = _wrap_wav(_peak_normalize(pcm))
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            "https://api.deepgram.com/v1/listen",
            params=params,
            headers=headers,
            content=body,
        )
        if r.status_code >= 400:
            log.error("deepgram http=%s body=%s", r.status_code, r.text[:300])
            r.raise_for_status()
        data = r.json()
        try:
            alt = data["results"]["channels"][0]["alternatives"][0]
            text = alt.get("transcript", "") or ""
            conf = alt.get("confidence", 0)
            dur = data.get("metadata", {}).get("duration", 0)
            if not text:
                log.warning(
                    "deepgram empty result conf=%.2f bytes=%d duration=%.2fs",
                    conf, len(body), dur,
                )
                # dump audio for debugging
                dump = f"/tmp/dg_empty_{int(time.time())}.wav"
                try:
                    with open(dump, "wb") as f:
                        f.write(body)
                    log.warning("deepgram empty audio dumped to %s", dump)
                except Exception:
                    pass
                log.warning("deepgram raw response head: %s", str(data)[:400])
            else:
                log.info("deepgram ok conf=%.2f duration=%.2fs", conf, dur)
            return text
        except (KeyError, IndexError) as e:
            log.error("deepgram parse error %s: %s", e, str(data)[:300])
            return ""


async def _transcribe_wyoming(pcm: bytes) -> str:
    async with AsyncTcpClient(STT_HOST, STT_PORT) as client:
        await client.write_event(Transcribe(language="es").event())
        await client.write_event(
            AudioStart(rate=SAMPLE_RATE, width=2, channels=CHANNELS).event()
        )
        chunk_bytes = SAMPLE_RATE * 2  # 1s @ 16k 16-bit
        for i in range(0, len(pcm), chunk_bytes):
            piece = pcm[i : i + chunk_bytes]
            await client.write_event(
                AudioChunk(
                    audio=piece, rate=SAMPLE_RATE, width=2, channels=CHANNELS
                ).event()
            )
        await client.write_event(AudioStop().event())
        while True:
            event = await client.read_event()
            if event is None:
                return ""
            if Transcript.is_type(event.type):
                return Transcript.from_event(event).text


async def transcribe(pcm: bytes) -> str:
    if not pcm:
        return ""
    if DEEPGRAM_API_KEY:
        try:
            return await _transcribe_deepgram(pcm)
        except Exception as e:
            log.warning("deepgram failed (%s), falling back to wyoming", e)
    return await _transcribe_wyoming(pcm)


async def call_backend(transcript: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{BACKEND_URL}/v1/voice/turn",
            headers={"Authorization": f"Bearer {BACKEND_KEY}"},
            json={"satellite_id": SATELLITE_ID, "transcript": transcript},
        )
        if r.status_code >= 400:
            log.error("backend http=%s body=%s", r.status_code, r.text[:300])
            r.raise_for_status()
        return r.json()["text"]


async def call_backend_stream(transcript: str):
    """Async generator: yields (kind, text) for each NDJSON event from backend."""
    import json as _json
    headers = {"Authorization": f"Bearer {BACKEND_KEY}"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            f"{BACKEND_URL}/v1/voice/turn/stream",
            headers=headers,
            json={"satellite_id": SATELLITE_ID, "transcript": transcript},
        ) as r:
            if r.status_code >= 400:
                body = await r.aread()
                log.error("backend stream http=%s body=%s", r.status_code, body[:300])
                r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    evt = _json.loads(line)
                except Exception as e:
                    log.warning("bad ndjson line: %s err=%s", line[:120], e)
                    continue
                yield evt.get("type"), evt.get("text") or ""


async def speak(text: str):
    """Streams Piper output through pw-cat as raw PCM — first audio leaves the
    speaker as soon as Piper produces the first chunk (no wait for full WAV)."""
    if not text.strip():
        return
    log.info("tts text=%r", text)

    sample_rate = 22050
    width = 2
    channels = 1
    proc = None
    audio_bytes = 0
    t_synth_start = time.monotonic()
    t_first_chunk = None

    async with AsyncTcpClient(TTS_HOST, TTS_PORT) as client:
        await client.write_event(Synthesize(text=text).event())
        while True:
            event = await client.read_event()
            if event is None:
                break
            if AudioStart.is_type(event.type):
                start = AudioStart.from_event(event)
                sample_rate = start.rate
                width = start.width
                channels = start.channels
                proc = await asyncio.create_subprocess_exec(
                    "pw-cat",
                    "--playback",
                    "--raw",
                    f"--rate={sample_rate}",
                    f"--channels={channels}",
                    "--format=s16",
                    "-",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            elif AudioChunk.is_type(event.type):
                if proc is None or proc.stdin is None:
                    continue
                if t_first_chunk is None:
                    t_first_chunk = time.monotonic()
                    log.info("timing tts_first_token=%dms",
                             int((t_first_chunk - t_synth_start) * 1000))
                chunk = AudioChunk.from_event(event).audio
                audio_bytes += len(chunk)
                try:
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError) as e:
                    log.warning("tts pipe closed: %s", e)
                    break
            elif AudioStop.is_type(event.type):
                break

    audio_ms = int(audio_bytes / (sample_rate * width) * 1000) if audio_bytes else 0
    log.info("timing tts_synth_total=%dms audio=%dms",
             int((time.monotonic() - t_synth_start) * 1000), audio_ms)
    if proc is not None and proc.stdin is not None:
        try:
            proc.stdin.close()
        except Exception:
            pass
        await proc.wait()
    log.info("timing tts_total=%dms",
             int((time.monotonic() - t_synth_start) * 1000))


async def voice_chat_followup_pending() -> bool:
    """Asks the backend if we're still in voice_chat (to skip wake word)."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(
                f"{BACKEND_URL}/v1/voice/satellite/{SATELLITE_ID}/state",
                headers={"Authorization": f"Bearer {BACKEND_KEY}"},
            )
            return r.json().get("mode") == "voice_chat"
        except Exception:
            return False


async def speak_short(name: str):
    """Optional ding sound. Skips if wav doesn't exist."""
    path = Path(__file__).parent / f"{name}.wav"
    if path.exists():
        _play(str(path))


async def _run_speak_short(name: str):
    try:
        await speak_short(name)
    except Exception as e:
        log.warning("ding failed: %s", e)


async def main_loop():
    log.info("satellite start id=%s", SATELLITE_ID)
    log.info("satellite endpoints backend=%s stt=%s:%s tts=%s:%s",
             BACKEND_URL, STT_HOST, STT_PORT, TTS_HOST, TTS_PORT)
    stt_engine = "deepgram" if DEEPGRAM_API_KEY else "wyoming"
    while True:
        try:
            in_chat = await voice_chat_followup_pending()
            preroll = b""
            if not in_chat:
                t0 = time.monotonic()
                preroll = await detect_wake_word()
                log.info("timing wake_wait=%dms preroll=%dms",
                         int((time.monotonic() - t0) * 1000),
                         int(len(preroll) / (SAMPLE_RATE * 2) * 1000))
                if _DING_ENABLED:
                    asyncio.create_task(_run_speak_short("ding"))

            t0 = time.monotonic()
            try:
                pcm = await asyncio.to_thread(record_until_silence)
            except Exception as e:
                log.exception("record failed: %s", e)
                continue
            pcm = preroll + pcm  # prepend preroll so the start of speech isn't lost
            rec_ms = int((time.monotonic() - t0) * 1000)
            audio_ms = int(len(pcm) / (SAMPLE_RATE * 2) * 1000)
            log.info("timing vad_record=%dms audio=%dms", rec_ms, audio_ms)

            if audio_ms == 0:
                log.info("turn skip reason=no-speech")
                continue

            t0 = time.monotonic()
            try:
                transcript = await transcribe(pcm)
            except Exception as e:
                log.exception("transcribe failed: %s", e)
                continue
            stt_ms = int((time.monotonic() - t0) * 1000)
            rtf = stt_ms / audio_ms if audio_ms else 0
            log.info("stt engine=%s text=%r", stt_engine, transcript)
            log.info("timing stt=%dms rtf=%.2f", stt_ms, rtf)

            if not transcript.strip():
                log.info("turn skip reason=empty-transcript")
                continue

            # streaming backend — events arrive as soon as they're produced; a
            # background "player" consumes them so prelim can TTS+play in parallel
            # with the next LLM iteration / tool call running on the backend.
            t_backend_start = time.monotonic()
            play_q: asyncio.Queue = asyncio.Queue()

            async def player():
                t_first = None
                idx = 0
                while True:
                    item = await play_q.get()
                    if item is None:
                        return
                    kind, text = item
                    if t_first is None:
                        t_first = time.monotonic()
                        log.info("timing time_to_first_token=%dms",
                                 int((t_first - t_backend_start) * 1000))
                    log.info("speak kind=%s idx=%d text=%r", kind, idx, text)
                    idx += 1
                    try:
                        await speak(text)
                    except Exception as e:
                        log.exception("speak failed: %s", e)

            player_task = asyncio.create_task(player())
            try:
                async for kind, text in call_backend_stream(transcript):
                    if kind == "done":
                        break
                    if kind == "error":
                        log.error("backend stream error: %s", text)
                        break
                    if not text:
                        continue
                    play_q.put_nowait((kind, text))
                log.info("timing backend_stream_total=%dms",
                         int((time.monotonic() - t_backend_start) * 1000))
            except Exception as e:
                log.exception("backend stream failed: %s", e)
            finally:
                play_q.put_nowait(None)
                await player_task
            log.info("timing turn_total=%dms",
                     int((time.monotonic() - t_backend_start) * 1000))
        except Exception as e:
            log.exception("loop error: %s", e)
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main_loop())
