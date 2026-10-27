"""Live check of the Sarvam WebSocket TTS: prints connect time, first-audio time and bytes. Needs SARVAM_API_KEY."""
import asyncio
import base64
import time

from apps.api.channels.voice import sarvam_client


async def main() -> None:
    t0 = time.monotonic()
    async with sarvam_client.SarvamWSTTSSession() as s:
        await s.send_text("Namaste, main Veerox se bol rahi hoon. ")
        await s.send_text("Aap kaise hain?")
        await s.finish()
        n = 0
        async for c in s.audio_chunks():
            n += len(base64.b64decode(c))
        print("connect_ms", s.connect_ms, "first_audio_ms", s.first_audio_ms, "bytes", n,
              "final", s.final_seen, "failed", s.failed, s.fail_reason, "total_ms", round((time.monotonic() - t0) * 1000))


asyncio.run(main())
