import logging
import os
import json
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# ======= НАСТРОЙКИ =======
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8926969111:AAEXGrYSAZPTrXjFaDGt7jKeh3sfevqVAI8")
SPREADSHEET_ID = "1LAB1eRocsBXulOqWu0lTJAK13mJdmcD2SEQGOstEfAk"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Читаем credentials из переменной окружения или файла
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

logging.basicConfig(level=logging.INFO)

# ======= МЕНЮ =======
MAIN_MENU = ReplyKeyboardMarkup([
    ["➕ Факт — добавить", "📋 Факт — просмотр", "✏️ Факт — изменить"],
    ["➕ План — добавить", "📋 План — просмотр", "✏️ План — изменить"],
], resize_keyboard=True)

# ======= ШАГИ =======
DATE, TYPE, CATEGORY, AMOUNT, COMMENT = range(5)
EDIT_SELECT, EDIT_FIELD, EDIT_VALUE = range(10, 13)
VIEW_DATE = 20

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

def format_amount(val):
    try:
        return f"{int(float(val)):,}"
    except:
        return str(val)

# ======= СТАРТ =======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Привет! Выбери действие:", reply_markup=MAIN_MENU)

# ============================================================
# УНИВЕРСАЛЬНОЕ ДОБАВЛЕНИЕ
# ============================================================
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    context.user_data["target_sheet"] = "fact" if "Факт" in text else "plan"
    today = datetime.now().strftime("%d.%m.%Y")
    keyboard = [[today], ["Другая дата"]]
    await update.message.reply_text("📅 Дата:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Другая дата":
        await update.message.reply_text("✏️ Введи дату (01.06.2026):", reply_markup=ReplyKeyboardRemove())
        return DATE
    context.user_data["date"] = text.strip()
    keyboard = [["💸 Расход", "💰 Доход"]]
    await update.message.reply_text("📊 Тип:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return TYPE

async def get_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text not in ["💸 Расход", "💰 Доход"]:
        await update.message.reply_text("⚠️ Выбери из кнопок!")
        return TYPE
    context.user_data["type"] = "Расход" if "Расход" in text else "Доход"
    sheet = sheet_fact if context.user_data["target_sheet"] == "fact" else sheet_plan
    cats = get_categories(sheet)
    keyboard = [[cat] for cat in cats] + [["✏️ Новая категория"]]
    await update.message.reply_text("🏷 Категория:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return CATEGORY

async def get_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "✏️ Новая категория":
        await update.message.reply_text("✏️ Введи название:", reply_markup=ReplyKeyboardRemove())
        return CATEGORY
    context.user_data["category"] = text
    await update.message.reply_text("💵 Сумма:", reply_markup=ReplyKeyboardRemove())
    return AMOUNT

async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace(",", ".").replace(" ", "")
    try:
        context.user_data["amount"] = float(text)
    except:
        await update.message.reply_text("❌ Введи число!")
        return AMOUNT
    keyboard = [["Пропустить"]]
    await update.message.reply_text("💬 Комментарий:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return COMMENT

async def get_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    comment = "" if text == "Пропустить" else text
    data = context.user_data
    sheet = sheet_fact if data["target_sheet"] == "fact" else sheet_plan
    row = [data["date"], data["type"], data["category"], data["amount"], comment]
    try:
        next_r = get_next_row(sheet)
        sheet.update(f"A{next_r}:E{next_r}", [row], value_input_option="USER_ENTERED")
        emoji = "💸" if data["type"] == "Расход" else "💰"
        label = "Факт" if data["target_sheet"] == "fact" else "План"
        await update.message.reply_text(
            f"✅ {label} сохранён!\n\n{emoji} {data['type']}\n📅 {data['date']}\n🏷 {data['category']}\n💵 {format_amount(data['amount'])}\n💬 {comment or '—'}",
            reply_markup=MAIN_MENU
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=MAIN_MENU)
    return ConversationHandler.END

# ============================================================
# УНИВЕРСАЛЬНЫЙ ПРОСМОТР
# ============================================================
async def view_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    context.user_data["target_sheet"] = "fact" if "Факт" in text else "plan"
    today = datetime.now().strftime("%d.%m.%Y")
    keyboard = [[today], ["Другая дата"], ["Все записи"]]
    await update.message.reply_text("📅 За какую дату?", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return VIEW_DATE

async def view_get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    sheet = sheet_fact if context.user_data["target_sheet"] == "fact" else sheet_plan
    label = "Факт" if context.user_data["target_sheet"] == "fact" else "План"

    if text == "Все записи":
        rows = get_all_rows(sheet)
        if not rows:
            await update.message.reply_text(f"📋 {label}: нет записей.", reply_markup=MAIN_MENU)
            return ConversationHandler.END
        # Группируем по дате
        by_date = {}
        for r in rows:
            by_date.setdefault(r["date"], []).append(r)
        msg = f"📋 *{label} — все записи:*\n\n"
        total_r, total_d = 0, 0
        for date, date_rows in list(by_date.items())[-10:]:  # последние 10 дат
            msg += f"📅 *{date}*\n"
            for r in date_rows:
                emoji = "💸" if r["type"] == "Расход" else "💰"
                msg += f"  {emoji} {r['category']} — {format_amount(r['amount'])}\n"
                try:
                    a = int(float(r["amount"]))
                    if r["type"] == "Расход": total_r += a
                    else: total_d += a
                except: pass
        msg += f"\n💸 Расходы: *{total_r:,}*\n💰 Доходы: *{total_d:,}*"
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_MENU)
        return ConversationHandler.END

    if text == "Другая дата":
        await update.message.reply_text("✏️ Введи дату (01.06.2026):", reply_markup=ReplyKeyboardRemove())
        return VIEW_DATE

    rows = get_rows_by_date(sheet, text.strip())
    if not rows:
        await update.message.reply_text(f"📋 За {text} записей нет.", reply_markup=MAIN_MENU)
        return ConversationHandler.END

    msg = f"📋 *{label} за {text}:*\n\n"
    total_r, total_d = 0, 0
    for i, r in enumerate(rows, 1):
        emoji = "💸" if r["type"] == "Расход" else "💰"
        comment = f" — {r['comment']}" if r["comment"] else ""
        msg += f"{i}. {emoji} {r['type']} | {r['category']} | {format_amount(r['amount'])}{comment}\n"
        try:
            a = int(float(r["amount"]))
            if r["type"] == "Расход": total_r += a
            else: total_d += a
        except: pass
    msg += f"\n💸 Расходы: *{total_r:,}*\n💰 Доходы: *{total_d:,}*"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_MENU)
    return ConversationHandler.END

# ============================================================
# УНИВЕРСАЛЬНОЕ ИЗМЕНЕНИЕ
# ============================================================
async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    context.user_data["target_sheet"] = "fact" if "Факт" in text else "plan"
    today = datetime.now().strftime("%d.%m.%Y")
    keyboard = [[today], ["Другая дата"]]
    await update.message.reply_text("📅 За какую дату изменить?", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return EDIT_SELECT

async def edit_select_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Другая дата":
        await update.message.reply_text("✏️ Введи дату:", reply_markup=ReplyKeyboardRemove())
        return EDIT_SELECT
    sheet = sheet_fact if context.user_data["target_sheet"] == "fact" else sheet_plan
    rows = get_rows_by_date(sheet, text.strip())
    if not rows:
        await update.message.reply_text(f"За {text} записей нет.", reply_markup=MAIN_MENU)
        return ConversationHandler.END
    context.user_data["edit_rows"] = rows
    keyboard = [[f"{i}. {r['type']} | {r['category']} | {format_amount(r['amount'])}"] for i, r in enumerate(rows, 1)]
    await update.message.reply_text("Выбери запись:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return EDIT_FIELD

async def edit_select_row(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        idx = int(text.split(".")[0]) - 1
        context.user_data["edit_row"] = context.user_data["edit_rows"][idx]
        keyboard = [["Тип", "Категория"], ["Сумма", "Комментарий"]]
        r = context.user_data["edit_row"]
        await update.message.reply_text(
            f"Что изменить?\n{r['type']} | {r['category']} | {format_amount(r['amount'])}",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return EDIT_VALUE
    except:
        await update.message.reply_text("⚠️ Выбери из списка!")
        return EDIT_FIELD

async def edit_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = update.message.text
    if field not in ["Тип", "Категория", "Сумма", "Комментарий"]:
        await update.message.reply_text("⚠️ Выбери из кнопок!")
        return EDIT_VALUE
    context.user_data["edit_field"] = field
    if field == "Тип":
        keyboard = [["💸 Расход", "💰 Доход"]]
        await update.message.reply_text("Новый тип:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    else:
        await update.message.reply_text(f"Новое значение для '{field}':", reply_markup=ReplyKeyboardRemove())
    return EDIT_VALUE + 1

async def edit_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    field = context.user_data["edit_field"]
    row_info = context.user_data["edit_row"]
    row_num = row_info["row_num"]
    sheet = sheet_fact if context.user_data["target_sheet"] == "fact" else sheet_plan
    col_map = {"Тип": 2, "Категория": 3, "Сумма": 4, "Комментарий": 5}
    col = col_map[field]
    value = "Расход" if "Расход" in text else ("Доход" if "Доход" in text else text)
    try:
        sheet.update_cell(row_num, col, value)
        await update.message.reply_text(f"✅ '{field}' изменено на: {value}", reply_markup=MAIN_MENU)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=MAIN_MENU)
    return ConversationHandler.END

# ============================================================
# ОТМЕНА
# ============================================================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.", reply_markup=MAIN_MENU)
    return ConversationHandler.END

# ============================================================
# ЗАПУСК
# ============================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv_add = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(➕ Факт — добавить|➕ План — добавить)$"), add_start)],
        states={
            DATE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
            TYPE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, get_type)],
            CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_category)],
            AMOUNT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            COMMENT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, get_comment)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    conv_view = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(📋 Факт — просмотр|📋 План — просмотр)$"), view_start)],
        states={
            VIEW_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, view_get_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    conv_edit = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(✏️ Факт — изменить|✏️ План — изменить)$"), edit_start)],
        states={
            EDIT_SELECT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_select_date)],
            EDIT_FIELD:      [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_select_row)],
            EDIT_VALUE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_save)],
            EDIT_VALUE + 1:  [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_final)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_add)
    app.add_handler(conv_view)
    app.add_handler(conv_edit)

    print("🤖 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
