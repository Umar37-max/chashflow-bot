import logging
import os
import json
import time
import asyncio
import requests
import fitz  # PyMuPDF — конвертация PDF-экспорта дашборда в PNG (НОВОЕ)
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest  # НОВОЕ
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo  # НОВОЕ

# ======= НАСТРОЙКИ =======
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8926969111:AAGoobIK7jj1TSj0LMi3n_PB1_4Qj-shpvE")
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
sheet_dashboard = spreadsheet.worksheet("📊 Дашборд")  # НОВОЕ: лист дашборда для скриншотов

logging.basicConfig(level=logging.INFO)

# ============================================================
# НОВОЕ: НАСТРОЙКИ СКРИНШОТА ДАШБОРДА
# ============================================================
DASHBOARD_DAY_CELL = "B4"     # ячейка "Выберите день месяца" (выпадающий список)
DASHBOARD_DATE_CELL = "B5"    # ячейка "Выбранная дата" (подпись к фото)
DASHBOARD_RANGE = "A1:O48"    # диапазон скриншота. Поправь, если что-то обрезается/много пустого места
SCREENSHOT_RECALC_DELAY = 3   # сек. ожидания пересчёта формул/графиков после смены дня
TASHKENT_TZ = ZoneInfo("Asia/Tashkent")  # часовой пояс для 08:00 / 20:00
CHAT_ID_FILE = "chat_id.txt"  # сюда сохраняется чат для авто-отправки в 8:00/20:00


def save_chat_id(chat_id):
    """Сохраняем chat_id, чтобы знать, кому слать дашборд в 8:00/20:00."""
    try:
        with open(CHAT_ID_FILE, "w") as f:
            f.write(str(chat_id))
    except Exception as e:
        logging.error(f"Не удалось сохранить chat_id: {e}")


def load_chat_id():
    """Берём chat_id либо из переменной окружения ADMIN_CHAT_ID, либо из файла."""
    env_id = os.environ.get("ADMIN_CHAT_ID")
    if env_id:
        return env_id
    try:
        with open(CHAT_ID_FILE, "r") as f:
            val = f.read().strip()
            return val or None
    except Exception:
        return None


def parse_day_from_text(text):
    """
    Принимает либо просто число дня (например '24'),
    либо дату вида '24.06.2026' / '24.06', и возвращает день месяца (int) или None.
    """
    text = text.strip()
    if text.isdigit():
        d = int(text)
        if 1 <= d <= 31:
            return d
        return None
    try:
        day_part = text.split(".")[0].strip()
        d = int(day_part)
        if 1 <= d <= 31:
            return d
    except Exception:
        pass
    return None


def export_dashboard_png():
    """
    Экспортирует диапазон DASHBOARD_RANGE листа 'Дашборд' как PDF через
    стандартный экспорт Google Таблиц, затем конвертирует первую страницу в PNG.
    """
    creds.refresh(GoogleAuthRequest())
    token = creds.token
    gid = sheet_dashboard.id

    export_url = (
        f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export"
        f"?format=pdf"
        f"&gid={gid}"
        f"&range={DASHBOARD_RANGE}"
        f"&size=A4"
        f"&portrait=false"
        f"&fitw=true"
        f"&gridlines=false"
        f"&printtitle=false"
        f"&sheetnames=false"
        f"&pagenum=UNDEFINED"
        f"&attachment=false"
        f"&scale=4"
        f"&top_margin=0.15&bottom_margin=0.15&left_margin=0.15&right_margin=0.15"
    )

    resp = requests.get(export_url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    if "pdf" not in content_type.lower():
        raise RuntimeError(
            "Google не вернул PDF (нет доступа у сервисного аккаунта или истёк токен). "
            f"Content-Type: {content_type}"
        )

    pdf_doc = fitz.open(stream=resp.content, filetype="pdf")
    page = pdf_doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))  # x3 для резкости
    png_bytes = pix.tobytes("png")
    pdf_doc.close()
    return png_bytes


def make_dashboard_screenshot_sync(day: int):
    """
    1) Запоминает текущий день в DASHBOARD_DAY_CELL
    2) Ставит нужный day
    3) Ждёт пересчёта формул/графиков
    4) Делает скриншот (PNG)
    5) Возвращает день на место (даже если был сбой)
    Возвращает (png_bytes, выбранная_дата_строка)
    """
    old_value = sheet_dashboard.acell(DASHBOARD_DAY_CELL).value
    try:
        sheet_dashboard.update_acell(DASHBOARD_DAY_CELL, int(day))
        time.sleep(SCREENSHOT_RECALC_DELAY)
        png_bytes = export_dashboard_png()
        selected_date = sheet_dashboard.acell(DASHBOARD_DATE_CELL).value
    finally:
        try:
            if old_value not in (None, ""):
                sheet_dashboard.update_acell(DASHBOARD_DAY_CELL, int(old_value))
        except Exception as restore_err:
            logging.error(f"Не удалось вернуть дату дашборда на место: {restore_err}")
    return png_bytes, selected_date


# ======= МЕНЮ =======
MAIN_MENU = ReplyKeyboardMarkup([
    ["➕ Факт", "📋 Факт просмотр"],
    ["✏️ Факт изменить", "🗑 Факт удалить"],
    ["➕ План", "📋 План просмотр"],
    ["✏️ План изменить", "🗑 План удалить"],
    ["📸 Скриншот дашборда"],  # НОВОЕ: кнопка скриншота
], resize_keyboard=True)

CANCEL_MENU = ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)

# ======= ШАГИ =======
DATE, TYPE, CATEGORY, AMOUNT, COMMENT = range(5)
EDIT_DATE, EDIT_SELECT, EDIT_FIELD, EDIT_VALUE = range(10, 14)
DEL_DATE, DEL_SELECT = range(30, 32)
VIEW_DATE = 20
SCREENSHOT_DATE = 40  # НОВОЕ

# ======= ВСПОМОГАТЕЛЬНЫЕ =======
def get_categories(sheet):
    try:
        vals = sheet.get_all_values()
        cats = set()
        for row in vals[4:]:
            if len(row) > 2 and row[2].strip():
                cats.add(row[2].strip())
        return sorted(list(cats))
    except:
        return []

def get_next_row(sheet):
    try:
        col_a = sheet.col_values(1)
        return len(col_a) + 1
    except:
        return 5

def get_rows_by_date(sheet, date_str):
    try:
        vals = sheet.get_all_values()
        result = []
        for i, row in enumerate(vals[4:], start=5):
            if len(row) > 3 and row[0].strip() == date_str:
                result.append({
                    "row_num": i,
                    "date": row[0],
                    "type": row[1],
                    "category": row[2],
                    "amount": row[3],
                    "comment": row[4] if len(row) > 4 else ""
                })
        return result
    except:
        return []

def get_all_rows(sheet):
    try:
        vals = sheet.get_all_values()
        result = []
        for i, row in enumerate(vals[4:], start=5):
            if len(row) > 3 and row[0].strip():
                result.append({
                    "row_num": i,
                    "date": row[0],
                    "type": row[1],
                    "category": row[2],
                    "amount": row[3],
                    "comment": row[4] if len(row) > 4 else ""
                })
        return result
    except:
        return []

def fmt(val):
    try:
        # убираем пробелы и запятые из числа
        clean = str(val).replace(" ", "").replace(",", ".")
        return f"{int(float(clean)):,}"
    except:
        return str(val)

def to_num(val):
    try:
        return int(float(str(val).replace(" ", "").replace(",", ".")))
    except:
        return 0

# ======= СТАРТ =======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Привет! Выбери действие:", reply_markup=MAIN_MENU)

# ======= ОТМЕНА =======
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.", reply_markup=MAIN_MENU)
    return ConversationHandler.END

# ============================================================
# НОВОЕ: ЗАХВАТ CHAT_ID (для автоматической отправки в 8:00/20:00)
# ============================================================
async def capture_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Срабатывает на ЛЮБОЕ сообщение в отдельной группе обработчиков (group=-1),
    поэтому никак не мешает остальным хендлерам и ничего в них не меняет.
    Просто запоминает, куда слать автоматический дашборд.
    """
    if update.effective_chat:
        save_chat_id(update.effective_chat.id)

# ============================================================
# НОВОЕ: СКРИНШОТ ДАШБОРДА ПО КНОПКЕ
# ============================================================
async def screenshot_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TASHKENT_TZ).strftime("%d.%m.%Y")
    kb = [[today], ["Другая дата"], ["❌ Отмена"]]
    await update.message.reply_text(
        "📅 За какую дату сделать скриншот дашборда?\n"
        "Можно написать число дня (например 24) или дату (например 24.06.2026).",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return SCREENSHOT_DATE

async def screenshot_get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)
    if text == "Другая дата":
        await update.message.reply_text(
            "✏️ Введи дату (например 24.06.2026) или просто день месяца (например 24):",
            reply_markup=CANCEL_MENU
        )
        return SCREENSHOT_DATE

    day = parse_day_from_text(text)
    if day is None:
        await update.message.reply_text(
            "⚠️ Не понял дату. Введи число дня (1-31) или дату в формате 24.06.2026."
        )
        return SCREENSHOT_DATE

    await update.message.reply_text("⏳ Делаю скриншот дашборда, подожди немного...", reply_markup=MAIN_MENU)

    try:
        loop = asyncio.get_event_loop()
        png_bytes, selected_date = await loop.run_in_executor(None, make_dashboard_screenshot_sync, day)
        caption_date = selected_date or text.strip()
        await update.message.reply_photo(
            photo=png_bytes,
            caption=f"📊 Дашборд за {caption_date}",
            reply_markup=MAIN_MENU
        )
    except Exception as e:
        logging.error(f"Ошибка скриншота дашборда: {e}")
        await update.message.reply_text(f"❌ Не удалось сделать скриншот: {e}", reply_markup=MAIN_MENU)

    return ConversationHandler.END

# ============================================================
# НОВОЕ: АВТОМАТИЧЕСКАЯ ОТПРАВКА В 8:00 И 20:00
# ============================================================
async def send_daily_dashboard_screenshot(context: ContextTypes.DEFAULT_TYPE):
    chat_id = load_chat_id()
    if not chat_id:
        logging.warning("Нет сохранённого chat_id — пропускаю автоматическую отправку дашборда. "
                         "Напиши боту /start хотя бы раз, либо задай ADMIN_CHAT_ID.")
        return

    today_day = datetime.now(TASHKENT_TZ).day
    try:
        loop = asyncio.get_event_loop()
        png_bytes, selected_date = await loop.run_in_executor(None, make_dashboard_screenshot_sync, today_day)
        now_str = datetime.now(TASHKENT_TZ).strftime("%d.%m.%Y %H:%M")
        caption_date = selected_date or datetime.now(TASHKENT_TZ).strftime("%d.%m.%Y")
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=png_bytes,
            caption=f"📊 Дашборд за {caption_date}\n🕒 Автоотправка: {now_str}"
        )
    except Exception as e:
        logging.error(f"Ошибка автоматической отправки дашборда: {e}")
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Не удалось сделать автоматический скриншот дашборда: {e}"
            )
        except Exception:
            pass

# ============================================================
# ДОБАВИТЬ
# ============================================================
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    context.user_data["target"] = "fact" if "Факт" in text else "plan"
    today = datetime.now().strftime("%d.%m.%Y")
    kb = [[today], ["Другая дата"], ["❌ Отмена"]]
    await update.message.reply_text(
        "📅 Дата:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)
    if text == "Другая дата":
        await update.message.reply_text("✏️ Введи дату (01.06.2026):", reply_markup=CANCEL_MENU)
        return DATE
    context.user_data["date"] = text.strip()
    kb = [["💸 Расход", "💰 Доход"], ["❌ Отмена"]]
    await update.message.reply_text(
        "📊 Тип:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return TYPE

async def get_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)
    if text not in ["💸 Расход", "💰 Доход"]:
        await update.message.reply_text("⚠️ Выбери из кнопок!")
        return TYPE
    context.user_data["type"] = "Расход" if "Расход" in text else "Доход"
    sheet = sheet_fact if context.user_data["target"] == "fact" else sheet_plan
    cats = get_categories(sheet)
    kb = [[cat] for cat in cats] + [["✏️ Новая категория"], ["❌ Отмена"]]
    await update.message.reply_text(
        "🏷 Категория:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return CATEGORY

async def get_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)
    if text == "✏️ Новая категория":
        await update.message.reply_text("✏️ Введи название:", reply_markup=CANCEL_MENU)
        return CATEGORY
    context.user_data["category"] = text
    await update.message.reply_text("💵 Сумма:", reply_markup=CANCEL_MENU)
    return AMOUNT

async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)
    try:
        context.user_data["amount"] = float(text.replace(",", ".").replace(" ", ""))
    except:
        await update.message.reply_text("❌ Введи число! Например: 15000")
        return AMOUNT
    kb = [["Пропустить"], ["❌ Отмена"]]
    await update.message.reply_text(
        "💬 Комментарий:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return COMMENT

async def get_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)
    comment = "" if text == "Пропустить" else text
    data = context.user_data
    sheet = sheet_fact if data["target"] == "fact" else sheet_plan
    row = [data["date"], data["type"], data["category"], data["amount"], comment]
    try:
        next_r = get_next_row(sheet)
        sheet.update(f"A{next_r}:E{next_r}", [row], value_input_option="USER_ENTERED")
        emoji = "💸" if data["type"] == "Расход" else "💰"
        label = "Факт" if data["target"] == "fact" else "План"
        await update.message.reply_text(
            f"✅ {label} сохранён!\n\n"
            f"{emoji} {data['type']}\n"
            f"📅 {data['date']}\n"
            f"🏷 {data['category']}\n"
            f"💵 {fmt(data['amount'])}\n"
            f"💬 {comment or '—'}",
            reply_markup=MAIN_MENU
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=MAIN_MENU)
    return ConversationHandler.END

# ============================================================
# ПРОСМОТР
# ============================================================
async def view_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    context.user_data["target"] = "fact" if "Факт" in text else "plan"
    today = datetime.now().strftime("%d.%m.%Y")
    kb = [[today], ["Другая дата"], ["Все записи"], ["❌ Отмена"]]
    await update.message.reply_text(
        "📅 За какую дату?",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return VIEW_DATE

async def view_get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)
    sheet = sheet_fact if context.user_data["target"] == "fact" else sheet_plan
    label = "Факт" if context.user_data["target"] == "fact" else "План"

    if text == "Другая дата":
        await update.message.reply_text("✏️ Введи дату (01.06.2026):", reply_markup=CANCEL_MENU)
        return VIEW_DATE

    if text == "Все записи":
        rows = get_all_rows(sheet)
    else:
        rows = get_rows_by_date(sheet, text.strip())

    if not rows:
        await update.message.reply_text(f"📋 Записей нет.", reply_markup=MAIN_MENU)
        return ConversationHandler.END

    total_r, total_d = 0, 0
    if text == "Все записи":
        by_date = {}
        for r in rows:
            by_date.setdefault(r["date"], []).append(r)
        msg = f"📋 *{label} — последние записи:*\n\n"
        for date, date_rows in list(by_date.items())[-7:]:
            msg += f"📅 *{date}*\n"
            for r in date_rows:
                e = "💸" if r["type"] == "Расход" else "💰"
                msg += f"  {e} {r['category']} — {fmt(r['amount'])}\n"
                n = to_num(r["amount"])
                if r["type"] == "Расход": total_r += n
                else: total_d += n
    else:
        msg = f"📋 *{label} за {text}:*\n\n"
        for i, r in enumerate(rows, 1):
            e = "💸" if r["type"] == "Расход" else "💰"
            c = f" — {r['comment']}" if r["comment"] else ""
            msg += f"{i}. {e} {r['type']} | {r['category']} | {fmt(r['amount'])}{c}\n"
            n = to_num(r["amount"])
            if r["type"] == "Расход": total_r += n
            else: total_d += n

    msg += f"\n💸 Расходы: *{total_r:,}*\n💰 Доходы: *{total_d:,}*"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_MENU)
    return ConversationHandler.END

# ============================================================
# ИЗМЕНИТЬ
# ============================================================
async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    context.user_data["target"] = "fact" if "Факт" in text else "plan"
    today = datetime.now().strftime("%d.%m.%Y")
    kb = [[today], ["Другая дата"], ["❌ Отмена"]]
    await update.message.reply_text(
        "📅 За какую дату изменить?",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return EDIT_DATE

async def edit_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)
    if text == "Другая дата":
        await update.message.reply_text("✏️ Введи дату:", reply_markup=CANCEL_MENU)
        return EDIT_DATE
    sheet = sheet_fact if context.user_data["target"] == "fact" else sheet_plan
    rows = get_rows_by_date(sheet, text.strip())
    if not rows:
        await update.message.reply_text(f"За {text} записей нет.", reply_markup=MAIN_MENU)
        return ConversationHandler.END
    context.user_data["edit_rows"] = rows
    kb = [[f"{i}. {r['type']} | {r['category']} | {fmt(r['amount'])}"] for i, r in enumerate(rows, 1)]
    kb.append(["❌ Отмена"])
    await update.message.reply_text(
        "Выбери запись:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return EDIT_SELECT

async def edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)
    try:
        idx = int(text.split(".")[0]) - 1
        context.user_data["edit_row"] = context.user_data["edit_rows"][idx]
        r = context.user_data["edit_row"]
        kb = [["Тип", "Категория"], ["Сумма", "Комментарий"], ["❌ Отмена"]]
        await update.message.reply_text(
            f"Что изменить?\n{r['type']} | {r['category']} | {fmt(r['amount'])}",
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
        return EDIT_FIELD
    except:
        await update.message.reply_text("⚠️ Выбери из списка!")
        return EDIT_SELECT

async def edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)
    if text not in ["Тип", "Категория", "Сумма", "Комментарий"]:
        await update.message.reply_text("⚠️ Выбери из кнопок!")
        return EDIT_FIELD
    context.user_data["edit_field"] = text
    if text == "Тип":
        kb = [["💸 Расход", "💰 Доход"], ["❌ Отмена"]]
        await update.message.reply_text(
            "Новый тип:",
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
        )
    else:
        await update.message.reply_text(f"Новое значение для '{text}':", reply_markup=CANCEL_MENU)
    return EDIT_VALUE

async def edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)
    field = context.user_data["edit_field"]
    row_info = context.user_data["edit_row"]
    sheet = sheet_fact if context.user_data["target"] == "fact" else sheet_plan
    col_map = {"Тип": 2, "Категория": 3, "Сумма": 4, "Комментарий": 5}
    col = col_map[field]
    value = "Расход" if "Расход" in text else ("Доход" if "Доход" in text else text)
    try:
        sheet.update_cell(row_info["row_num"], col, value)
        await update.message.reply_text(f"✅ '{field}' изменено на: {value}", reply_markup=MAIN_MENU)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=MAIN_MENU)
    return ConversationHandler.END


# ============================================================
# УДАЛИТЬ
# ============================================================
async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    context.user_data["target"] = "fact" if "Факт" in text else "plan"
    today = datetime.now().strftime("%d.%m.%Y")
    kb = [[today], ["Другая дата"], ["❌ Отмена"]]
    await update.message.reply_text(
        "📅 За какую дату удалить?",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return DEL_DATE

async def delete_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)
    if text == "Другая дата":
        await update.message.reply_text("✏️ Введи дату:", reply_markup=CANCEL_MENU)
        return DEL_DATE
    sheet = sheet_fact if context.user_data["target"] == "fact" else sheet_plan
    rows = get_rows_by_date(sheet, text.strip())
    if not rows:
        await update.message.reply_text(f"За {text} записей нет.", reply_markup=MAIN_MENU)
        return ConversationHandler.END
    context.user_data["del_rows"] = rows
    kb = [[f"{i}. {r['type']} | {r['category']} | {fmt(r['amount'])}"] for i, r in enumerate(rows, 1)]
    kb.append(["❌ Отмена"])
    await update.message.reply_text(
        "Выбери запись для удаления:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return DEL_SELECT

async def delete_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)
    try:
        idx = int(text.split(".")[0]) - 1
        rows = context.user_data["del_rows"]
        row_info = rows[idx]
        sheet = sheet_fact if context.user_data["target"] == "fact" else sheet_plan
        sheet.update(f"A{row_info['row_num']}:E{row_info['row_num']}", [["", "", "", "", ""]])
        await update.message.reply_text(
            f"✅ Удалено: {row_info['type']} | {row_info['category']} | {fmt(row_info['amount'])}",
            reply_markup=MAIN_MENU
        )
    except:
        await update.message.reply_text("⚠️ Выбери из списка!", reply_markup=MAIN_MENU)
    return ConversationHandler.END

# ============================================================
# ЗАПУСК
# ============================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv_add = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(➕ Факт|➕ План)$"), add_start)],
        states={
            DATE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
            TYPE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, get_type)],
            CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_category)],
            AMOUNT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            COMMENT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, get_comment)],
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^❌ Отмена$"), cancel)]
    )

    conv_view = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(📋 Факт просмотр|📋 План просмотр)$"), view_start)],
        states={
            VIEW_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, view_get_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^❌ Отмена$"), cancel)]
    )

    conv_edit = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(✏️ Факт изменить|✏️ План изменить)$"), edit_start)],
        states={
            EDIT_DATE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_date)],
            EDIT_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_select)],
            EDIT_FIELD:  [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field)],
            EDIT_VALUE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^❌ Отмена$"), cancel)]
    )

    conv_delete = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(🗑 Факт удалить|🗑 План удалить)$"), delete_start)],
        states={
            DEL_DATE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_date)],
            DEL_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_select)],
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^❌ Отмена$"), cancel)]
    )

    # НОВОЕ: диалог скриншота дашборда по кнопке
    conv_screenshot = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📸 Скриншот дашборда$"), screenshot_start)],
        states={
            SCREENSHOT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, screenshot_get_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^❌ Отмена$"), cancel)]
    )

    # НОВОЕ: ловим chat_id с любого сообщения в отдельной группе -1,
    # чтобы это не мешало остальным хендлерам (они в группе 0 по умолчанию)
    app.add_handler(MessageHandler(filters.ALL, capture_chat_id), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_add)
    app.add_handler(conv_view)
    app.add_handler(conv_edit)
    app.add_handler(conv_delete)
    app.add_handler(conv_screenshot)  # НОВОЕ

    # НОВОЕ: автоматическая отправка дашборда в 8:00 и 20:00 по Ташкенту
    if app.job_queue is not None:
        app.job_queue.run_daily(
            send_daily_dashboard_screenshot,
            time=dtime(hour=8, minute=0, tzinfo=TASHKENT_TZ)
        )
        app.job_queue.run_daily(
            send_daily_dashboard_screenshot,
            time=dtime(hour=20, minute=0, tzinfo=TASHKENT_TZ)
        )
    else:
        print("⚠️ JobQueue не установлен. Выполни: pip install \"python-telegram-bot[job-queue]\"")

    print("🤖 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
