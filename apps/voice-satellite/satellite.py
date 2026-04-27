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
import os
import sys
import tempfile
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


def _play(path: str) -> None:
    if sys.platform == "darwin":
        os.system(f"afplay {path}")
    elif sys.platform.startswith("linux"):
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

VAD_SILENCE_MS = 800  # ms of silence to stop recording
MAX_RECORD_S = 12  # hard cutoff
RMS_SILENCE_THRESHOLD = 350  # tune empirically with your mic


async def detect_wake_word():
    """Blocks until wake word is detected. Streams audio from mic."""
    print("[wake] listening...")
    detected = asyncio.Event()

    async with AsyncTcpClient(WAKE_HOST, WAKE_PORT) as client:
        await client.write_event(
            AudioStart(rate=SAMPLE_RATE, width=2, channels=CHANNELS).event()
        )

        loop = asyncio.get_event_loop()
        q: asyncio.Queue = asyncio.Queue()

        def callback(indata, frames, time_info, status):
            loop.call_soon_threadsafe(q.put_nowait, indata.copy())

        async def reader():
            while not detected.is_set():
                try:
                    event = await asyncio.wait_for(client.read_event(), timeout=0.1)
                    if event and Detection.is_type(event.type):
                        print(f"[wake] detected: {Detection.from_event(event).name}")
                        detected.set()
                        return
                except TimeoutError:
                    continue

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=callback,
            blocksize=1024,
        ):
            reader_task = asyncio.create_task(reader())
            try:
                while not detected.is_set():
                    chunk = await q.get()
                    audio = chunk.tobytes()
                    await client.write_event(
                        AudioChunk(
                            audio=audio, rate=SAMPLE_RATE, width=2, channels=CHANNELS
                        ).event()
                    )
            finally:
                reader_task.cancel()
                await client.write_event(AudioStop().event())


def record_until_silence() -> bytes:
    """Records until prolonged silence or cap. Returns PCM 16-bit."""
    print("[record] listening...")
    chunks = []
    silence_ms = 0
    total_ms = 0
    chunk_ms = 30
    chunk_samples = int(SAMPLE_RATE * chunk_ms / 1000)

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        blocksize=chunk_samples,
    ) as stream:
        speech_started = False
        while True:
            data, _ = stream.read(chunk_samples)
            samples = data.flatten().astype(np.int32)
            rms = int(np.sqrt(np.mean(samples * samples))) if len(samples) else 0

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
            if total_ms >= MAX_RECORD_S * 1000:
                break

    print(f"[record] done, {len(chunks)} chunks")
    return b"".join(chunks)


async def transcribe(pcm: bytes) -> str:
    if not pcm:
        return ""
    async with AsyncTcpClient(STT_HOST, STT_PORT) as client:
        await client.write_event(Transcribe(language="es").event())
        await client.write_event(
            AudioStart(rate=SAMPLE_RATE, width=2, channels=CHANNELS).event()
        )
        # send in 1-second chunks
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


async def call_backend(transcript: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{BACKEND_URL}/v1/voice/turn",
            headers={"Authorization": f"Bearer {BACKEND_KEY}"},
            json={"satellite_id": SATELLITE_ID, "transcript": transcript},
        )
        r.raise_for_status()
        return r.json()["text"]


async def speak(text: str):
    if not text.strip():
        return
    print(f"[tts] {text}")

    pcm_chunks: list[bytes] = []
    sample_rate = 22050
    width = 2
    channels = 1

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
            elif AudioChunk.is_type(event.type):
                pcm_chunks.append(AudioChunk.from_event(event).audio)
            elif AudioStop.is_type(event.type):
                break

    if pcm_chunks:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            with wave.open(f.name, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(width)
                wf.setframerate(sample_rate)
                wf.writeframes(b"".join(pcm_chunks))
            _play(f.name)
            os.unlink(f.name)


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


async def main_loop():
    print(f"[satellite] Mauricio voice satellite '{SATELLITE_ID}' starting...")
    print(f"[satellite] backend={BACKEND_URL}  stt={STT_HOST}:{STT_PORT}  tts={TTS_HOST}:{TTS_PORT}")  # noqa: E501
    while True:
        try:
            in_chat = await voice_chat_followup_pending()
            if not in_chat:
                await detect_wake_word()
                await speak_short("ding")
            pcm = record_until_silence()
            transcript = await transcribe(pcm)
            print(f"[stt] {transcript}")
            if not transcript.strip():
                continue
            response = await call_backend(transcript)
            await speak(response)
        except Exception as e:
            print(f"[loop] error: {e}", file=sys.stderr)
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main_loop())
