
import os
import base64
import mimetypes
import hmac
import logging

import httpx
from fastapi import FastAPI, Request, HTTPException
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OWNER_ID = os.getenv("OWNER_TELEGRAM_ID", "").strip()
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")
BASE_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

SYSTEM_PROMPT = """
تو TradeMate هستی؛ دستیار تخصصی تحلیل بازارهای مالی.

بازارهای قابل بررسی:
فارکس، طلا، نقره، نفت، کالاها، ارز دیجیتال،
سهام، شاخص‌ها و اوراق قرضه.

وظیفه تو دو بخش دارد:
1. تحلیل مستقل نمودار و شرایط بازار.
2. بررسی تحلیل کاربر، پیدا کردن نقاط قوت، اشتباهات و تناقض‌ها.

اصول مهم:
- هیچ قیمت زنده، خبر، حجم یا داده‌ای را جعل نکن.
- اگر اطلاعات کافی نیست، واضح بگو چه چیزی کم است.
- از روی عکس، نماد و تایم‌فریم را فقط در صورت قابل تشخیص بودن اعلام کن.
- اگر چیزی در تصویر واضح نیست، حدس قطعی نزن.
- احتمال عددی و درصد موفقیت ساختگی ارائه نکن.
- بین واقعیت قابل مشاهده و فرضیه تفاوت بگذار.
- تحلیل را ساده، دقیق و بدون تناقض بنویس.
- هیچ‌وقت سود را تضمین نکن.
- اگر معامله‌ای شرایط مناسبی ندارد، صریح بگو ورود نکن.

قالب پاسخ تحلیل:

1. نماد و تایم‌فریم:
2. وضعیت کلی بازار و جهت احتمالی:
3. ساختار بازار:
4. حمایت و مقاومت‌های مهم:
5. نواحی مهم مانند Order Block و FVG، در صورت قابل تشخیص بودن:
6. سناریوی صعودی:
7. سناریوی نزولی:
8. شرط تأیید ورود:
9. نقطه ابطال سناریو:
10. مدیریت ریسک و هشدارها:
11. میزان اطمینان کیفی: کم، متوسط یا زیاد؛ همراه با دلیل.

اگر کاربر تحلیل خودش را فرستاد:
ابتدا تحلیل او را بررسی کن؛ سپس تحلیل مستقل خودت را ارائه بده
و در پایان تفاوت این دو تحلیل را توضیح بده.

اگر کاربر سؤال آموزشی پرسید، کوتاه و دقیق آموزش بده.
"""
app = FastAPI()
ai = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


async def telegram_api(method, data=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, json=data or {})
        response.raise_for_status()
        result = response.json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")

    return result.get("result")


async def send_message(chat_id, text):
    text = str(text)
    for start in range(0, len(text), 4000):
        await telegram_api("sendMessage", {
            "chat_id": chat_id,
            "text": text[start:start + 4000]
        })


async def ask_ai(prompt, image_data=None):
    if ai is None:
        return "کلید OpenAI هنوز در تنظیمات هاست ثبت نشده است."

    if image_data:
        content = [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": image_data}
        ]
        response = await ai.responses.create(
            model=MODEL,
            instructions=SYSTEM_PROMPT,
            input=[{"role": "user", "content": content}]
        )
    else:
        response = await ai.responses.create(
            model=MODEL,
            instructions=SYSTEM_PROMPT,
            input=prompt
        )

    return response.output_text or "پاسخی دریافت نشد."


async def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if text == "/id":
        await send_message(chat_id, f"شناسه تلگرام شما:\n{chat_id}")
        return

    if text == "/start":
        await send_message(
            chat_id,
            "سلام! من TradeMate هستم.\n"
            "می‌توانی سؤال معاملاتی بپرسی یا عکس چارت بفرستی.\n"
            "برای دریافت شناسه تلگرام دستور /id را بفرست."
        )
        return

    if OWNER_ID and str(chat_id) != OWNER_ID:
        await send_message(chat_id, "این ربات به صورت خصوصی تنظیم شده است.")
        return

    if not OWNER_ID:
        await send_message(
            chat_id,
            "برای فعال‌سازی ربات، ابتدا دستور /id را بفرست "
            "و شناسه‌ات را در تنظیمات هاست ثبت کن."
        )
        return

    try:
        if message.get("photo"):
            photo = message["photo"][-1]
            file_info = await telegram_api("getFile", {
                "file_id": photo["file_id"]
            })

            file_path = file_info["file_path"]
            file_url = (
                f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            )

            async with httpx.AsyncClient(timeout=60) as client:
                image_response = await client.get(file_url)
                image_response.raise_for_status()

            mime_type = mimetypes.guess_type(file_path)[0] or "image/jpeg"
            encoded = base64.b64encode(image_response.content).decode("utf-8")
            image_data = f"data:{mime_type};base64,{encoded}"

            caption = message.get("caption", "").strip()
            prompt = caption or (
                "این نمودار را تحلیل کن. نماد و تایم‌فریم را "
                "در صورت امکان تشخیص بده."
            )

            answer = await ask_ai(prompt, image_data)

        elif text:
            answer = await ask_ai(text)

        else:
            await send_message(
                chat_id,
                "لطفاً متن یا عکس نمودار ارسال کن."
            )
            return

        await send_message(chat_id, answer)

    except Exception:
        logger.exception("Message processing failed")
        await send_message(
            chat_id,
            "در تحلیل خطایی رخ داد. کمی بعد دوباره تلاش کن."
        )


@app.get("/")
async def health():
    return {"status": "TradeMate is running"}


@app.post("/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if not WEBHOOK_SECRET or not hmac.compare_digest(
        secret, WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    received_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token", ""
    )

    if not hmac.compare_digest(received_secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden")

    update = await request.json()
    message = update.get("message")

    if message:
        await handle_message(message)

    return {"ok": True}


@app.on_event("startup")
async def setup_webhook():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    if not WEBHOOK_SECRET:
        raise RuntimeError("TELEGRAM_WEBHOOK_SECRET is missing")

    if not BASE_URL:
        raise RuntimeError("Render URL is missing")

    await telegram_api("setWebhook", {
        "url": f"{BASE_URL}/telegram/{WEBHOOK_SECRET}",
        "secret_token": WEBHOOK_SECRET,
        "allowed_updates": ["message"]
    })

    logger.info("Telegram webhook configured")
