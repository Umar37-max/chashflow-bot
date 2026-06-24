import logging
import os
import json
import io
import asyncio
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from PIL import Image

# Playwright для скриншотов
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logging.warning("Playwright не установлен. Скриншоты недоступны.")

# ======= НАСТРОЙКИ =======
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8926969111:AAGoobIK7jj1TSj0LMi3n_PB1_4Qj-shpvE")
SPREADSHEET_ID = "1LAB1eRocsBXulOqWu0lTJAK13mJdmcD2SEQGOstEfAk"

# Telegram USER ID для авто-отправки скриншотов (замените на свой)
OWNER_CHAT_ID = os.environ.get("5674083773", "")

# URL дашборда (лист Дашборд)
DASHBOARD_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit?pli=1#gid=1154"

# Диапазон для скриншота — ячейка B4 (день месяца)
DAY_CELL = "B4"

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
sheet_dashboard = spreadsheet.worksheet("Дашборд")

logging.basicConfig(level=logging.INFO)

# ======= МЕНЮ =======
MAIN_MENU = ReplyKeyboardMarkup([
    ["➕ Факт", "📋 Факт просмотр"],
    ["✏️ Факт изменить", "🗑 Факт удалить"],
    ["➕ План", "📋 План просмотр"],
    ["✏️ План изменить", "🗑 План удалить"],
    ["📸 Скриншот дашборда"],
], resize_keyboard=True)

CANCEL_MENU = ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)

# ======= ШАГИ =======
DATE, TYPE, CATEGORY, AMOUNT, COMMENT = range(5)
EDIT_DATE, EDIT_SELECT, EDIT_FIELD, EDIT_VALUE = range(10, 14)
DEL_DATE, DEL_SELECT = range(30, 32)
VIEW_DATE = 20
SCREENSHOT_DATE = 40

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
        clean = str(val).replace(" ", "").replace(",", ".")
        return f"{int(float(clean)):,}"
    except:
        return str(val)

def to_num(val):
    try:
        return int(float(str(val).replace(" ", "").replace(",", ".")))
    except:
        return 0

# ======= СКРИНШОТ ДАШБОРДА =======
def set_dashboard_day(day: int):
    """Ставит нужный день в ячейку B4 дашборда"""
    try:
        sheet_dashboard.update_cell(4, 2, day)
        return True
    except Exception as e:
        logging.error(f"Ошибка при смене дня в дашборде: {e}")
        return False

def get_dashboard_day():
    """Читает текущий день из ячейки B4 дашборда"""
    try:
        val = sheet_dashboard.cell(4, 2).value
        return int(float(str(val))) if val else datetime.now().day
    except:
        return datetime.now().day

async def make_dashboard_screenshot(date_str: str) -> bytes | None:
    """
    Делает скриншот дашборда для указанной даты.
    date_str: "24.06.2026"
    Возвращает bytes PNG или None при ошибке.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return None

    try:
        # Парсим день из даты
        day = int(date_str.split(".")[0])
    except:
        day = datetime.now().day

    # Читаем текущий день чтобы восстановить потом
    original_day = get_dashboard_day()

    # Ставим нужный день
    set_dashboard_day(day)
    # Ждём пересчёта формул
    await asyncio.sleep(3)

    screenshot_bytes = None

    try:
        google_creds_env = os.environ.get("GOOGLE_CREDENTIALS")
        google_email = None
        google_password = os.environ.get("GOOGLE_PASSWORD", "")

        if google_creds_env:
            creds_info_local = json.loads(google_creds_env)
            # Для логина через браузер нужен обычный Google аккаунт
            # Используем переменные окружения GOOGLE_EMAIL и GOOGLE_PASSWORD
            google_email = os.environ.get("GOOGLE_EMAIL", "")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                viewport={"width": 1600, "height": 900},
                locale="ru-RU"
            )
            page = await context.new_page()

            # Логин в Google если есть email/password
            if google_email and google_password:
                await page.goto("https://accounts.google.com/signin")
                await page.wait_for_load_state("networkidle")

                # Ввод email
                await page.fill('input[type="email"]', google_email)
                await page.click('#identifierNext')
                await page.wait_for_timeout(2000)

                # Ввод пароля
                await page.fill('input[type="password"]', google_password)
                await page.click('#passwordNext')
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(3000)

            # Открываем дашборд
            await page.goto(DASHBOARD_URL, wait_until="networkidle")
            await page.wait_for_timeout(5000)

            # Ждём загрузки таблицы
            try:
                await page.wait_for_selector(".grid-container", timeout=15000)
            except:
                await page.wait_for_timeout(5000)

            # Делаем скриншот всей страницы
            screenshot_bytes = await page.screenshot(
                full_page=False,
                clip={
                    "x": 0,
                    "y": 0,
                    "width": 1600,
                    "height": 900
                }
            )

            await browser.close()

    except Exception as e:
        logging.error(f"Ошибка playwright скриншота: {e}")
        screenshot_bytes = None
    finally:
        # Восстанавливаем оригинальный день
        await asyncio.sleep(1)
        set_dashboard_day(original_day)

    return screenshot_bytes


async def make_screenshot_via_export(date_str: str) -> bytes | None:
    """
    Альтернативный метод: скриншот через Google Sheets Export API.
    Экспортирует лист Дашборд как PDF/PNG через сервисный аккаунт.
    """
    try:
        day = int(date_str.split(".")[0])
    except:
        day = datetime.now().day

    original_day = get_dashboard_day()
    set_dashboard_day(day)
    await asyncio.sleep(4)  # Ждём пересчёта

    try:
        import requests
        from google.auth.transport.requests import Request

        # Обновляем credentials
        creds_local = creds
        if creds_local.expired and creds_local.refresh_token:
            creds_local.refresh(Request())

        # Получаем gid листа Дашборд
        try:
            dashboard_gid = sheet_dashboard.id
        except:
            dashboard_gid = 1154  # fallback из URL

        # URL для экспорта листа как PNG
        export_url = (
            f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export"
            f"?format=png"
            f"&gid={dashboard_gid}"
            f"&range=A1:N60"  # диапазон дашборда
            f"&size=A3"
            f"&portrait=false"
            f"&fitw=true"
        )

        headers = {"Authorization": f"Bearer {creds_local.token}"}
        response = requests.get(export_url, headers=headers, timeout=30)

        if response.status_code == 200:
            return response.content
        else:
            logging.error(f"Export API вернул {response.status_code}: {response.text[:200]}")
            return None

    except Exception as e:
        logging.error(f"Ошибка export скриншота: {e}")
        return None
    finally:
        await asyncio.sleep(1)
        set_dashboard_day(original_day)


async def get_dashboard_screenshot(date_str: str) -> bytes | None:
    """
    Основная функция получения скриншота.
    Сначала пробует export API (быстрее), затем Playwright.
    """
    # Сначала пробуем быстрый метод через Google Export API
    img_bytes = await make_screenshot_via_export(date_str)
    if img_bytes:
        return img_bytes

    # Если не получилось — пробуем Playwright
    if PLAYWRIGHT_AVAILABLE:
        img_bytes = await make_dashboard_screenshot(date_str)
        if img_bytes:
            return img_bytes

    return None


# ======= АВТО-ОТПРАВКА СКРИНШОТА =======
async def send_scheduled_screenshot(application):
    """Отправляет скриншот дашборда владельцу бота"""
    if not OWNER_CHAT_ID:
        logging.warning("OWNER_CHAT_ID не задан — авто-скриншот пропущен")
        return

    today = datetime.now().strftime("%d.%m.%Y")
    hour = datetime.now().hour
    time_label = "🌅 Утро" if hour < 12 else "🌆 Вечер"

    await application.bot.send_message(
        chat_id=OWNER_CHAT_ID,
        text=f"📊 {time_label} — дашборд за {today}\nГотовлю скриншот..."
    )

    try:
        img_bytes = await get_dashboard_screenshot(today)
        if img_bytes:
            await application.bot.send_photo(
                chat_id=OWNER_CHAT_ID,
                photo=io.BytesIO(img_bytes),
                caption=f"📊 {time_label} | Финансовый дашборд — {today}"
            )
        else:
            await application.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text="❌ Не удалось сделать скриншот. Проверь настройки."
            )
    except Exception as e:
        logging.error(f"Ошибка авто-отправки скриншота: {e}")
        await application.bot.send_message(
            chat_id=OWNER_CHAT_ID,
            text=f"❌ Ошибка при отправке скриншота: {e}"
        )


def schedule_screenshots(application):
    """Настраивает расписание авто-скриншотов: 8:00 и 20:00"""
    job_queue = application.job_queue

    # 8:00 утра
    job_queue.run_daily(
        lambda ctx: asyncio.create_task(send_scheduled_screenshot(application)),
        time=datetime.strptime("08:00", "%H:%M").time(),
        name="morning_screenshot"
    )

    # 20:00 вечера
    job_queue.run_daily(
        lambda ctx: asyncio.create_task(send_scheduled_screenshot(application)),
        time=datetime.strptime("20:00", "%H:%M").time(),
        name="evening_screenshot"
    )

    logging.info("📅 Авто-скриншоты запланированы на 08:00 и 20:00")


# ======= СТАРТ =======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Привет! Выбери действие:", reply_markup=MAIN_MENU)

# ======= ОТМЕНА =======
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.", reply_markup=MAIN_MENU)
    return ConversationHandler.END

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
# СКРИНШОТ ДАШБОРДА (по запросу пользователя)
# ============================================================
async def screenshot_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().strftime("%d.%m.%Y")
    kb = [[today], ["Другая дата"], ["❌ Отмена"]]
    await update.message.reply_text(
        "📸 За какую дату сделать скриншот дашборда?",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return SCREENSHOT_DATE

async def screenshot_get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        return await cancel(update, context)

    if text == "Другая дата":
        await update.message.reply_text("✏️ Введи дату (01.06.2026):", reply_markup=CANCEL_MENU)
        return SCREENSHOT_DATE

    date_str = text.strip()

    await update.message.reply_text(
        f"⏳ Делаю скриншот дашборда за {date_str}...\nЭто займёт несколько секунд.",
        reply_markup=ReplyKeyboardRemove()
    )

    try:
        img_bytes = await get_dashboard_screenshot(date_str)

        if img_bytes:
            await update.message.reply_photo(
                photo=io.BytesIO(img_bytes),
                caption=f"📊 Финансовый дашборд — {date_str}",
                reply_markup=MAIN_MENU
            )
        else:
            await update.message.reply_text(
                "❌ Не удалось сделать скриншот.\n\n"
                "Проверь что:\n"
                "• Playwright установлен (`pip install playwright && playwright install chromium`)\n"
                "• Переменные GOOGLE_EMAIL и GOOGLE_PASSWORD заданы\n"
                "• Или что у сервисного аккаунта есть доступ к таблице",
                reply_markup=MAIN_MENU
            )
    except Exception as e:
        logging.error(f"Ошибка скриншота: {e}")
        await update.message.reply_text(
            f"❌ Ошибка при создании скриншота: {e}",
            reply_markup=MAIN_MENU
        )

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

    conv_screenshot = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📸 Скриншот дашборда$"), screenshot_start)],
        states={
            SCREENSHOT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, screenshot_get_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^❌ Отмена$"), cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_add)
    app.add_handler(conv_view)
    app.add_handler(conv_edit)
    app.add_handler(conv_delete)
    app.add_handler(conv_screenshot)

    # Авто-скриншоты в 8:00 и 20:00
    if OWNER_CHAT_ID:
        schedule_screenshots(app)
    else:
        logging.warning(
            "⚠️ OWNER_CHAT_ID не задан. Авто-скриншоты отключены. "
            "Задай переменную окружения OWNER_CHAT_ID=ваш_telegram_id"
        )

    print("🤖 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
