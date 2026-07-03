import logging
import os
import json
import time
import asyncio
import requests
import fitz  # PyMuPDF
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from flask import Flask, request
import threading

# ======= НАСТРОЙКИ =======
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Environment variable BOT_TOKEN is not set.")

SPREADSHEET_ID = "1LAB1eRocsBXulOqWu0lTJAK13mJdmcD2SEQGOstEfAk"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

google_creds_json = os.environ.get("GOOGLE_CREDENTIALS")
if google_creds_json:
    creds_info = json.loads(google_creds_json)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
else:
    creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)

gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SPREADSHEET_ID)
sheet_fact = spreadsheet.worksheet("Ввод Факт")
sheet_plan = spreadsheet.worksheet("План ввод")
sheet_dashboard = spreadsheet.worksheet("📊 Дашборд")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")
for _lg in ("httpx", "telegram", "apscheduler", "urllib3", "googleapiclient", "gspread", "flask", "werkzeug"):
    logging.getLogger(_lg).setLevel(logging.WARNING)

# WEBHOOK И FLASK
PORT = int(os.environ.get("PORT", 8000))
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL")
WEBHOOK_PATH = "/webhook"

# ДАШБОРД
DASHBOARD_DAY_CELL = "B4"
DASHBOARD_DATE_CELL = "B5"
DASHBOARD_RANGE = "A1:O48"
SCREENSHOT_RECALC_DELAY = 3
TASHKENT_TZ = ZoneInfo("Asia/Tashkent")
CHAT_ID_FILE = "chat_id.txt"

app_flask = Flask(__name__)
app_telegram = None

def save_chat_id(chat_id):
    try:
        with open(CHAT_ID_FILE, "w") as f:
            f.write(str(chat_id))
    except Exception as e:
        logging.error(f"Ошибка сохранения chat_id: {e}")


def load_chat_id():
    env_id = os.environ.get("ADMIN_CHAT_ID")
    if env_id:
        return env_id
    try:
        with open(CHAT_ID_FILE, "r") as f:
            return f.read().strip() or None
    except Exception:
        return None


def parse_day_from_text(text):
    text = text.strip()
    if text.isdigit():
        d = int(text)
        return d if 1 <= d <= 31 else None
    try:
        day_part = text.split(".")[0].strip()
        d = int(day_part)
        return d if 1 <= d <= 31 else None
    except Exception:
        return None


def export_dashboard_png(day_of_month):
    try:
        sheet_dashboard.update(DASHBOARD_DAY_CELL, day_of_month)
        time.sleep(SCREENSHOT_RECALC_DELAY)
        
        url = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=pdf&gid={sheet_dashboard.id}"
        response = requests.get(url)
        if response.status_code != 200:
            return None
        
        pdf_doc = fitz.open(stream=response.content, filetype="pdf")
        page = pdf_doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        
        png_path = "/tmp/dashboard.png"
        pix.save(png_path)
        pdf_doc.close()
        
        return png_path
    except Exception as e:
        logging.error(f"Ошибка экспорта дашборда: {e}")
        return None


# СОСТОЯНИЯ
ADD_DATE, ADD_TYPE, ADD_VALUE, ADD_COMMENT = range(4)
VIEW_CHOICE = 1
EDIT_DATE, EDIT_TYPE, EDIT_VALUE, EDIT_COMMENT = range(4)
DEL_DATE, DEL_SELECT = range(2)
SCREENSHOT_DATE = 1


async def capture_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_chat_id(update.effective_chat.id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["✏️ Добавить запись", "📖 Просмотр"],
        ["✏️ Редактировать", "🗑️ Удалить"],
        ["📸 Скриншот дашборда"]
    ]
    await update.message.reply_text("Выбери действие:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Тип записи:",
        reply_markup=ReplyKeyboardMarkup([["📊 Факт", "📈 План"], ["❌ Отмена"]], resize_keyboard=True)
    )
    return ADD_TYPE


async def add_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["type"] = "Факт" if update.message.text == "📊 Факт" else "План"
    await update.message.reply_text("Дата (24 или 24.06.2026):", reply_markup=ReplyKeyboardRemove())
    return ADD_DATE


async def add_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    day = parse_day_from_text(update.message.text)
    if not day:
        await update.message.reply_text("❌ Число от 1 до 31:")
        return ADD_DATE
    context.user_data["day"] = day
    await update.message.reply_text("Сумма:")
    return ADD_VALUE


async def add_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["value"] = float(update.message.text)
        await update.message.reply_text("Комментарий (или 'нет'):", reply_markup=ReplyKeyboardRemove())
        return ADD_COMMENT
    except ValueError:
        await update.message.reply_text("❌ Введи число:")
        return ADD_VALUE


async def add_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment = "" if update.message.text.lower() == "нет" else update.message.text
    try:
        sheet_name = "Ввод Факт" if context.user_data["type"] == "Факт" else "План ввод"
        sheet = spreadsheet.worksheet(sheet_name)
        sheet.append_row([context.user_data["day"], context.user_data["value"], comment])
        await update.message.reply_text("✅ Добавлено!", reply_markup=ReplyKeyboardMarkup([["✏️ Добавить запись", "📖 Просмотр"]], resize_keyboard=True))
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")
    return ConversationHandler.END


async def view_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Что смотрим?", reply_markup=ReplyKeyboardMarkup([["📊 Факт", "📈 План"], ["Все записи"], ["❌ Отмена"]], resize_keyboard=True))
    return VIEW_CHOICE


async def view_get_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Все записи":
        try:
            fact = sheet_fact.get_all_values()[1:]
            plan = sheet_plan.get_all_values()[1:]
            text = "📊 **ФАКТ:**\n"
            for row in fact[-10:]:
                if len(row) >= 2:
                    text += f"День {row[0]}: {row[1]}" + (f" - {row[2]}" if len(row) > 2 else "") + "\n"
            text += "\n📈 **ПЛАН:**\n"
            for row in plan[-10:]:
                if len(row) >= 2:
                    text += f"День {row[0]}: {row[1]}" + (f" - {row[2]}" if len(row) > 2 else "") + "\n"
            await update.message.reply_text(text or "Нет данных.")
        except Exception as e:
            logging.error(f"Ошибка: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    return ConversationHandler.END


async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Тип:", reply_markup=ReplyKeyboardMarkup([["📊 Факт", "📈 План"], ["❌ Отмена"]], resize_keyboard=True))
    return EDIT_TYPE


async def edit_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["type"] = "Факт" if update.message.text == "📊 Факт" else "План"
    await update.message.reply_text("День:")
    return EDIT_DATE


async def edit_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    day = parse_day_from_text(update.message.text)
    if not day:
        await update.message.reply_text("❌ Число 1-31:")
        return EDIT_DATE
    context.user_data["day"] = day
    await update.message.reply_text("Новая сумма:")
    return EDIT_VALUE


async def edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["value"] = float(update.message.text)
        await update.message.reply_text("Новый комментарий (или 'нет'):")
        return EDIT_COMMENT
    except ValueError:
        await update.message.reply_text("❌ Число:")
        return EDIT_VALUE


async def edit_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment = "" if update.message.text.lower() == "нет" else update.message.text
    try:
        sheet_name = "Ввод Факт" if context.user_data["type"] == "Факт" else "План ввод"
        sheet = spreadsheet.worksheet(sheet_name)
        rows = sheet.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == str(context.user_data["day"]):
                sheet.update_row(i, [context.user_data["day"], context.user_data["value"], comment])
                break
        await update.message.reply_text("✅ Обновлено!", reply_markup=ReplyKeyboardMarkup([["✏️ Редактировать", "🗑️ Удалить"]], resize_keyboard=True))
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")
    return ConversationHandler.END


async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Тип:", reply_markup=ReplyKeyboardMarkup([["📊 Факт", "📈 План"], ["❌ Отмена"]], resize_keyboard=True))
    return DEL_DATE


async def delete_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["type"] = "Факт" if update.message.text == "📊 Факт" else "План"
    await update.message.reply_text("День:")
    return DEL_SELECT


async def delete_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    day = parse_day_from_text(update.message.text)
    if not day:
        await update.message.reply_text("❌ Число 1-31:")
        return DEL_SELECT
    try:
        sheet_name = "Ввод Факт" if context.user_data["type"] == "Факт" else "План ввод"
        sheet = spreadsheet.worksheet(sheet_name)
        rows = sheet.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == str(day):
                sheet.delete_rows(i, i)
                break
        await update.message.reply_text("✅ Удалено!", reply_markup=ReplyKeyboardMarkup([["🗑️ Удалить", "❌ Отмена"]], resize_keyboard=True))
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")
    return ConversationHandler.END


async def screenshot_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("День (1-31):")
    return SCREENSHOT_DATE


async def screenshot_get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    day = parse_day_from_text(update.message.text)
    if not day:
        await update.message.reply_text("❌ Число 1-31:")
        return SCREENSHOT_DATE
    
    await update.message.reply_text("⏳ Генерирую...")
    png_path = export_dashboard_png(day)
    if png_path and os.path.exists(png_path):
        try:
            with open(png_path, "rb") as f:
                await update.message.reply_photo(f)
            os.remove(png_path)
        except Exception as e:
            logging.error(f"Ошибка: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}")
    else:
        await update.message.reply_text("❌ Не удалось сгенерировать.")
    return ConversationHandler.END


async def send_daily_dashboard_screenshot(context: ContextTypes.DEFAULT_TYPE):
    chat_id = load_chat_id()
    if not chat_id:
        return
    try:
        day = datetime.now(TASHKENT_TZ).day
        png_path = export_dashboard_png(day)
        if png_path and os.path.exists(png_path):
            with open(png_path, "rb") as f:
                await context.bot.send_photo(chat_id, f)
            os.remove(png_path)
    except Exception as e:
        logging.error(f"Ошибка авто-отправки: {e}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.", reply_markup=ReplyKeyboardMarkup([["✏️ Добавить запись", "📖 Просмотр"]], resize_keyboard=True))
    return ConversationHandler.END


@app_flask.route("/health", methods=["GET"])
def health_check():
    return "OK", 200


@app_flask.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        if data:
            update = Update.de_json(data, app_telegram.bot)
            asyncio.run(app_telegram.process_update(update))
        return "OK", 200
    except Exception as e:
        logging.error(f"Ошибка webhook: {e}")
        return "Error", 500


def keep_alive_thread():
    while True:
        try:
            time.sleep(20)
            if WEBHOOK_URL_BASE:
                requests.get(f"{WEBHOOK_URL_BASE}/health", timeout=5)
        except Exception:
            pass


async def main():
    global app_telegram
    
    app_telegram = Application.builder().token(BOT_TOKEN).build()
    
    conv_add = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✏️ Добавить запись$"), add_start)],
        states={
            ADD_TYPE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_type)],
            ADD_DATE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_date)],
            ADD_VALUE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, add_value)],
            ADD_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_comment)],
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^❌ Отмена$"), cancel)]
    )

    conv_view = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📖 Просмотр$"), view_start)],
        states={VIEW_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, view_get_choice)]},
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^❌ Отмена$"), cancel)]
    )

    conv_edit = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✏️ Редактировать$"), edit_start)],
        states={
            EDIT_TYPE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_type)],
            EDIT_DATE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_date)],
            EDIT_VALUE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value)],
            EDIT_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_comment)],
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^❌ Отмена$"), cancel)]
    )

    conv_delete = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🗑️ Удалить$"), delete_start)],
        states={
            DEL_DATE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_date)],
            DEL_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_select)],
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^❌ Отмена$"), cancel)]
    )

    conv_screenshot = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📸 Скриншот дашборда$"), screenshot_start)],
        states={SCREENSHOT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, screenshot_get_date)]},
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^❌ Отмена$"), cancel)]
    )

    app_telegram.add_handler(MessageHandler(filters.ALL, capture_chat_id), group=-1)
    app_telegram.add_handler(CommandHandler("start", start))
    app_telegram.add_handler(conv_add)
    app_telegram.add_handler(conv_view)
    app_telegram.add_handler(conv_edit)
    app_telegram.add_handler(conv_delete)
    app_telegram.add_handler(conv_screenshot)

    if app_telegram.job_queue is not None:
        app_telegram.job_queue.run_daily(send_daily_dashboard_screenshot, time=dtime(hour=8, minute=0, tzinfo=TASHKENT_TZ))
        app_telegram.job_queue.run_daily(send_daily_dashboard_screenshot, time=dtime(hour=20, minute=0, tzinfo=TASHKENT_TZ))

    webhook_url = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None
    if webhook_url:
        logging.info(f"Webhook: {webhook_url}")
        await app_telegram.bot.set_webhook(webhook_url, allowed_updates=Update.ALL_TYPES)
    
    threading.Thread(target=lambda: app_flask.run(host="0.0.0.0", port=PORT, debug=False), daemon=True).start()
    threading.Thread(target=keep_alive_thread, daemon=True).start()
    
    logging.info(f"Bot started (PORT {PORT}, webhook mode)")
    
    await app_telegram.initialize()
    await app_telegram.start()
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await app_telegram.stop()


if __name__ == "__main__":
    asyncio.run(main())
