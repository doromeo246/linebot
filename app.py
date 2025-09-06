import os, uuid, glob, asyncio
from datetime import datetime
from flask import Flask, request, abort
from googletrans import Translator
import edge_tts
from mutagen import File as MutagenFile
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, AudioMessage, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.webhook import WebhookHandler
from apscheduler.schedulers.background import BackgroundScheduler

# ======================== 環境變數 ========================
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
PORT = int(os.getenv("PORT", 5000))
EDGE_VOICE = os.getenv("EDGE_VOICE", "zh-TW-HsiaoChenNeural")
AUDIO_DIR = os.path.join("static", "audio")

# ======================== Flask ========================
app = Flask(__name__, static_url_path="/static", static_folder="static")
os.makedirs(AUDIO_DIR, exist_ok=True)

# ======================== LINE ========================
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print("Handler Error:", e)
        abort(400)
    return "OK"

# ======================== 翻譯 ========================
def translate_text(text: str, target_language: str = "zh-TW"):
    translator = Translator()
    try:
        result = translator.translate(text, dest=target_language)
        return result.text
    except Exception:
        return text

# ======================== TTS ========================
async def synth_edge_tts(text: str, out_path: str, voice: str):
    tts = edge_tts.Communicate(text=text, voice=voice)
    await tts.save(out_path)

def text_to_speech_edge(text: str, out_path: str, voice: str):
    asyncio.run(synth_edge_tts(text, out_path, voice))

# ======================== 回覆函式 ========================
def reply_text(reply_token, message):
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(messages=[TextMessage(text=message)], reply_token=reply_token)
        )

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event: MessageEvent):
    user_text = event.message.text.strip()

    # 翻譯
    translated_text = translate_text(user_text)

    # 產生語音
    filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}.mp3"
    filepath = os.path.join(AUDIO_DIR, filename)
    try:
        text_to_speech_edge(translated_text, filepath, EDGE_VOICE)
    except Exception as e:
        reply_text(event.reply_token, f"TTS 失敗：{e}")
        return

    # 語音長度
    try:
        duration_ms = int(MutagenFile(filepath).info.length * 1000)
    except Exception:
        duration_ms = max(1000, len(translated_text) * 160)

    # 回覆 LINE
    audio_url = f"{request.url_root}static/audio/{filename}"
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(text=f"翻譯結果：{translated_text}"),
                    AudioMessage(type="audio", original_content_url=audio_url, duration=duration_ms)
                ]
            )
        )

# ======================== 排程清理音檔 ========================
def cleanup_files():
    import time
    now = time.time()
    for f in glob.glob(os.path.join(AUDIO_DIR, "*.mp3")):
        if now - os.path.getmtime(f) > 1800:
            os.remove(f)
            print(f"已刪除 {f}")

scheduler = BackgroundScheduler()
scheduler.add_job(cleanup_files, "interval", minutes=10)
scheduler.start()

# ======================== 啟動 ========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)