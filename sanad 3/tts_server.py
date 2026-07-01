# -----------------------------------------------------------
# سيرفر صوت محلي صغير لواجهة سَند
# يستخدم مكتبة edge-tts (نفس الصوت العصبي: ar-SA-ZariyahNeural)
#
# طريقة التشغيل:
#   1) pip install flask edge-tts
#   2) python tts_server.py
#   3) اتركي هذا السيرفر شغّال، وافتحي sanad_chat.html بالمتصفح
#      وراح تسمعين صوت Edge TTS الحقيقي تلقائيًا.
#
# إذا ما شغّلتي هذا السيرفر، الواجهة ما راح تتعطل — بترجع
# تلقائيًا تستخدم صوت جوجل كبديل.
# -----------------------------------------------------------

import asyncio
import edge_tts
from flask import Flask, request, Response

app = Flask(__name__)

VOICE = "ar-SA-ZariyahNeural"


async def synthesize(text: str) -> bytes:
    communicate = edge_tts.Communicate(text=text, voice=VOICE)
    audio_bytes = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_bytes.extend(chunk["data"])
    return bytes(audio_bytes)


@app.after_request
def add_cors_headers(resp):
    # مطلوب عشان صفحة الـ HTML (اللي تفتح كملف محلي أو من أي origin)
    # تقدر تتواصل مع هذا السيرفر بدون ما يمنعها المتصفح
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp


@app.route("/tts")
def tts():
    text = request.args.get("text", "").strip()
    if not text:
        return Response("Missing 'text' query param", status=400)

    try:
        audio = asyncio.run(synthesize(text))
    except Exception as e:
        return Response(f"TTS error: {e}", status=500)

    return Response(audio, mimetype="audio/mpeg")


@app.route("/health")
def health():
    return {"status": "ok", "voice": VOICE}


if __name__ == "__main__":
    print("🎙️  سيرفر صوت سَند شغّال على http://localhost:8765")
    print(f"    الصوت المستخدم: {VOICE}")
    app.run(host="127.0.0.1", port=8765, threaded=True)
