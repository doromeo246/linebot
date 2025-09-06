# ====================================================
# 📌 圖文選單主程式 (Flask + LINE Bot + TTS + deep-translator + 回覆語音與文字)
# ====================================================

import os, uuid, asyncio, glob
from datetime import datetime
from flask import Flask, request, abort
from mutagen import File as MutagenFile
from deep_translator import GoogleTranslator
import edge_tts

# LINE v3 SDK
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest,
    AudioMessage, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.webhook import WebhookHandler

# ====================================================
# 🔧 環境變數
# ====================================================
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
PORT = int(os.getenv("PORT", "5000"))
EDGE_VOICE = os.getenv("EDGE_VOICE", "zh-TW-HsiaoChenNeural")
AUDIO_DIR = os.path.join("static", "audio")

# ====================================================
# 🚀 Flask
# ====================================================
app = Flask(__name__, static_url_path="/static", static_folder="static")
os.makedirs(AUDIO_DIR, exist_ok=True)

# ====================================================
# 🤖 LINE SDK 設定
# ====================================================
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    
    # 🔍 Debug：印出簽名與 body
    print("===== LINE Webhook Debug =====")
    print("Signature:", signature)
    print("Body:", body)
    print("================================")
    
    if not signature:
        print("❌ X-Line-Signature header missing!")
        abort(400, "X-Line-Signature header missing")
    
    if not body:
        print("❌ Request body is empty!")
        abort(400, "Request body is empty")
    
    try:
        handler.handle(body, signature)
    except Exception as e:
        print("❌ Handler Error:", e)
        abort(400, f"Handler error: {e}")
    
    return "OK"
# ====================================================
# 🌐 deep-translator 翻譯
# ====================================================
def translate_text(text: str, target_language: str = "zh-TW", source_language: str = "auto"):
    try:
        translated_text = GoogleTranslator(source=source_language, target=target_language).translate(text)
        return {"text": translated_text, "src": source_language, "dest": target_language}
    except Exception as e:
        print(f"⚠️ 翻譯失敗: {e}")
        return {"text": text, "src": source_language, "dest": target_language}

# ====================================================
# 🔊 Edge TTS (文字轉語音)
# ====================================================
async def synth_edge_tts(text: str, out_path: str, voice: str):
    tts = edge_tts.Communicate(text=text, voice=voice)
    await tts.save(out_path)

def text_to_speech_edge(text: str, out_path: str, voice: str):
    asyncio.run(synth_edge_tts(text, out_path, voice))

# ====================================================
# 📩 處理使用者訊息
# ====================================================
user_language = {}

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event: MessageEvent):
    user_id = event.source.user_id
    user_text = event.message.text.strip()

    # --- 使用者從圖文選單選擇語言 ---
    if user_text in ["英文", "日文", "韓文", "中文"]:
        lang_map = {
            "英文": ("en", "en-US-AriaNeural"),
            "日文": ("ja", "ja-JP-NanamiNeural"),
            "韓文": ("ko", "ko-KR-SunHiNeural"),
            "中文": ("zh-TW", "zh-TW-HsiaoChenNeural"),
        }
        user_language[user_id] = lang_map[user_text]
        reply_text(event.reply_token, f"✅ 已切換為 {user_text}，請輸入要轉換的文字")
        return

    # --- 如果使用者沒選語言，預設中文 ---
    if user_id not in user_language:
        user_language[user_id] = ("zh-TW", "zh-TW-HsiaoChenNeural")
        reply_text(event.reply_token, "⚠️ 未選語言，已自動設定為中文")

    target_lang, selected_voice = user_language[user_id]

    # 翻譯
    result = translate_text(user_text, target_language=target_lang)
    translated_text = result["text"]
    src_lang = result["src"]

    print(f"🌐 翻譯結果：{src_lang} ➝ {target_lang} | {translated_text}")

    if not translated_text:
        reply_text(event.reply_token, "❌ 翻譯失敗，請稍後再試。")
        return

    # 建立語音檔
    filename = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.mp3"
    filepath = os.path.join(AUDIO_DIR, filename)

    try:
        text_to_speech_edge(translated_text, filepath, selected_voice)
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"語音檔未生成: {filepath}")
    except Exception as e:
        reply_text(event.reply_token, f"❌ 語音合成失敗：{e}")
        return

    # 語音長度
    try:
        duration_ms = int(MutagenFile(filepath).info.length * 1000)
    except Exception:
        duration_ms = max(1000, int(len(translated_text) * 160))

    # 回覆 LINE 語音 + 翻譯文字
    audio_url = f"{request.host_url}static/audio/{filename}"
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(text=f"🌐 翻譯 ({src_lang} ➝ {target_lang})：\n{translated_text}"),
                    AudioMessage(
                        type="audio",
                        original_content_url=audio_url,
                        duration=duration_ms
                    )
                ]
            )
        )

# ====================================================
# 🧹 清理檔案
# ====================================================
def cleanup_files():
    print("\n🧹 清理 audio 資料夾...")
    mp3_files = glob.glob(os.path.join(AUDIO_DIR, "*.mp3"))
    for f in mp3_files:
        print(f" - 已刪除 {f}")
        os.remove(f)
    print("✅ 清理完畢")

# ====================================================
# 🛠️ 輔助函式
# ====================================================
def reply_text(reply_token, message):
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=message)]
            )
        )

# ====================================================
# 🚦 啟動 Flask
# ====================================================
try:
    app.run(host="0.0.0.0", port=PORT, debug=False)
except (KeyboardInterrupt, SystemExit):
    pass
finally:
    cleanup_files()


