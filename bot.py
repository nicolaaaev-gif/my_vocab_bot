import os
import logging
import random
import json
import re
from datetime import datetime, timedelta
from collections import defaultdict
from openai import OpenAI
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import asyncio
from telegram.request import HTTPXRequest

# === ЗАГРУЖАЕМ НАСТРОЙКИ ===
load_dotenv()
logging.basicConfig(level=logging.INFO)

# === ПОДКЛЮЧАЕМСЯ К DEEPSEEK ===
deepseek_client = OpenAI(
    api_key=os.environ.get('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com"
)

# === РАБОТА С ФАЙЛАМИ ===
WORDS_FILE = "words.json"
USER_DATA_FILE = "user_data.json"

def load_words():
    if os.path.exists(WORDS_FILE):
        with open(WORDS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        return []

def save_words(words_list):
    with open(WORDS_FILE, 'w', encoding='utf-8') as f:
        json.dump(words_list, f, ensure_ascii=False, indent=2)

def load_user_data():
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_user_data(data):
    with open(USER_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

words = load_words()
user_data = load_user_data()
user_histories = {}

# === ХРАНИЛИЩЕ СЕССИЙ ===
sessions = {}
scheduler = AsyncIOScheduler()

# === КОНСТАНТЫ ===
REVIEW_INTERVALS = {
    "review_1": 3,
    "review_2": 7,
    "review_3": 14
}
NEXT_STATUS = {
    "learning": "review_1",
    "review_1": "review_2",
    "review_2": "review_3",
    "review_3": "mastered"
}

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def split_text(text, max_length=4000):
    if len(text) <= max_length:
        return [text]
    parts = []
    current = ""
    for line in text.split('\n'):
        if len(current) + len(line) + 1 > max_length:
            parts.append(current)
            current = line
        else:
            current += '\n' + line if current else line
    if current:
        parts.append(current)
    return parts

def get_words_by_status(status):
    return [w for w in words if w.get("status") == status]

def get_words_by_statuses(statuses):
    return [w for w in words if w.get("status") in statuses]

def get_word_entry_by_text(word_text):
    for w in words:
        if w["word"].split(" — ")[0].lower() == word_text.lower():
            return w
    return None

def find_translation(word_text):
    word_lower = word_text.lower().strip()
    for w in words:
        full = w["word"]
        en_part = full.split(" — ")[0].lower()
        ru_part = full.split(" — ")[1].lower()
        if word_lower == en_part or word_lower == ru_part:
            return (full.split(" — ")[0], full.split(" — ")[1], full)
        if word_lower in en_part or word_lower in ru_part:
            return (full.split(" — ")[0], full.split(" — ")[1], full)
    return None

def get_hint(word, direction):
    en_part = word.split(" — ")[0]
    ru_part = word.split(" — ")[1]
    if direction == "ru_to_en":
        return f"💡 Подсказка: слово начинается с '{en_part[0].upper()}' и связано с изменением направления"
    else:
        return f"💡 Подсказка: слово начинается с '{ru_part[0].upper()}' и переводится как 'разворот'"

def update_word_status(word_text, new_status, set_review_date=True):
    for w in words:
        if w["word"].split(" — ")[0].lower() == word_text.lower():
            w["status"] = new_status
            if new_status == "learning":
                w["learned_at"] = datetime.now().isoformat()
            elif new_status in REVIEW_INTERVALS and set_review_date:
                days = REVIEW_INTERVALS[new_status]
                w["next_review_date"] = (datetime.now() + timedelta(days=days)).isoformat()
            elif new_status == "mastered":
                w["next_review_date"] = None
            save_words(words)
            return True
    return False

def get_daily_words(limit=5):
    new_words = get_words_by_status("new")
    if not new_words:
        return []
    selected = random.sample(new_words, min(limit, len(new_words)))
    for w in selected:
        w["status"] = "learning"
        w["learned_at"] = datetime.now().isoformat()
    save_words(words)
    return selected

def get_words_for_learn(limit=10):
    learning_words = get_words_by_status("learning")
    learning_words.sort(key=lambda x: x.get("learned_at", "1970-01-01"))
    
    now = datetime.now()
    review_statuses = ["review_1", "review_2", "review_3"]
    review_words = get_words_by_statuses(review_statuses)
    due_review = [w for w in review_words if w.get("next_review_date") and datetime.fromisoformat(w["next_review_date"]) <= now]
    due_review.sort(key=lambda x: x.get("next_review_date", "2099-01-01"))
    
    all_words = learning_words + due_review
    if not all_words:
        return []
    return all_words[:limit]

def get_words_for_practice(limit=10, difficulty=None):
    mastered = get_words_by_status("mastered")
    review = get_words_by_statuses(["review_1", "review_2", "review_3"])
    available = mastered + review
    if difficulty:
        available = [w for w in available if w.get("difficulty", "medium") == difficulty]
    if not available:
        return []
    return random.sample(available, min(limit, len(available)))

def get_learning_words():
    return get_words_by_status("learning")

def generate_examples_for_words(word_entries):
    if not word_entries:
        return "Нет слов для генерации примеров."
    
    prompt = f"""
    Для каждого из следующих слов напиши ровно 2 примера предложений на английском языке с переводом на русский.
    Предложения должны быть из IT-сферы (Product Management, аналитика, разработка, менеджмент).
    Слова: {', '.join([w['word'].split(' — ')[0] for w in word_entries])}

    Формат вывода (строго соблюдай):
    Слово: [слово]
    1. [предложение на английском] — [перевод на русский]
    2. [предложение на английском] — [перевод на русский]

    Повтори этот блок для каждого слова.
    """
    
    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"Ошибка генерации примеров: {e}")
        return None

def generate_practice_sentences(word_entries, count=10, difficulty="medium"):
    if not word_entries:
        return "Недостаточно слов для практики."
    word_list = "\n".join([f"• {w['word'].split(' — ')[0]}" for w in word_entries])
    prompt = f"""
    Составь {count} предложений на РУССКОМ языке для перевода на английский.
    Используй эти слова (каждое хотя бы один раз):
    {word_list}
    Формат: пронумерованный список от 1 до {count}.
    Предложения из IT-сферы (Product Management, аналитика, разработка).
    Сложность: {difficulty}.
    """
    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"Ошибка генерации: {e}")
        return "Ошибка генерации предложений."

def generate_cloze(word_entries, count=3):
    if not word_entries:
        return "Недостаточно слов."
    selected = random.sample(word_entries, min(count, len(word_entries)))
    word_list = "\n".join([f"• {w['word'].split(' — ')[0]}" for w in selected])
    prompt = f"""
    Составь 3 предложения на английском языке, где пропущены ключевые слова.
    Используй эти слова (каждое хотя бы один раз):
    {word_list}
    
    Формат:
    1. [предложение с ______ вместо слова]
    2. [предложение с ______ вместо слова]
    3. [предложение с ______ вместо слова]
    
    После каждого предложения дай перевод на русский.
    """
    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"Ошибка генерации cloze: {e}")
        return "Ошибка генерации."

def generate_dialogue(word_entries, topic="general"):
    if not word_entries:
        return "Недостаточно слов для диалога."
    word_list = "\n".join([f"• {w['word'].split(' — ')[0]}" for w in word_entries])
    prompt = f"""
    Создай короткий диалог (6-8 реплик) на английском языке между двумя коллегами в IT-компании.
    Тема: {topic}.
    Используй эти слова (каждое хотя бы один раз):
    {word_list}
    
    После диалога дай перевод на русский и выдели использованные слова.
    """
    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"Ошибка генерации диалога: {e}")
        return "Ошибка генерации диалога."

def check_translation(user_word, correct_word):
    prompt = f"""
    Пользователь перевёл слово.
    ПРАВИЛЬНЫЙ ОТВЕТ: {correct_word}
    ОТВЕТ ПОЛЬЗОВАТЕЛЯ: {user_word}
    Оцени: правильно или нет. Если ошибка — объясни кратко (максимум 2 предложения).
    """
    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return response.choices[0].message.content
    except:
        return f"✅ Правильный ответ: {correct_word}"

def check_sentences(user_message):
    prompt = f"""
    Проверь переводы пользователя (список предложений):
    {user_message}
    Оцени каждый, укажи ошибки, дай общий процент.
    """
    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return response.choices[0].message.content
    except:
        return "Не удалось проверить."

def detect_add_word(text):
    patterns = [
        r'добавь\s+слово\s+(.+?)\s*[-—]\s*(.+?)(?:\s+#(\w+))?',
        r'запомни\s+слово\s+(.+?)\s*[-—]\s*(.+?)(?:\s+#(\w+))?',
        r'выучи\s+слово\s+(.+?)\s*[-—]\s*(.+?)(?:\s+#(\w+))?',
        r'добавь\s+(.+?)\s*[-—]\s*(.+?)(?:\s+#(\w+))?\s*к\s+обучению',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            word = match.group(1).strip()
            trans = match.group(2).strip()
            topic = match.group(3).lower() if match.group(3) else None
            return (word, trans, topic)
    if 'добавь' in text.lower() and '—' in text:
        parts = text.split('—')
        if len(parts) == 2:
            word = parts[0].replace('добавь', '').replace('слово', '').strip()
            trans = parts[1].strip()
            topic_match = re.search(r'#(\w+)', trans)
            if topic_match:
                topic = topic_match.group(1).lower()
                trans = trans.replace(f"#{topic}", "").strip()
            else:
                topic = None
            if word and trans:
                return (word, trans, topic)
    return None

def detect_status_command(text):
    match = re.search(r'/status\s+(.+?)\s+[-—]\s+(.+)', text, re.IGNORECASE)
    if match:
        return (match.group(1).strip(), match.group(2).strip().lower())
    return None

def ask_deepseek(user_id, text):
    if user_id not in user_histories:
        user_histories[user_id] = [
            {"role": "system", "content": "Ты репетитор английского для IT. Объясняй слова, давай примеры."}
        ]
    user_histories[user_id].append({"role": "user", "content": text})
    try:
        resp = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=user_histories[user_id],
            stream=False
        )
        ans = resp.choices[0].message.content
        user_histories[user_id].append({"role": "assistant", "content": ans})
        if len(user_histories[user_id]) > 20:
            user_histories[user_id] = [user_histories[user_id][0]] + user_histories[user_id][-19:]
        return ans
    except:
        return "Ошибка."

# === КНОПКИ ===
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("📖 Взять новые слова", callback_data="menu_daily")],
        [InlineKeyboardButton("🧠 Тренировка", callback_data="menu_learn")],
        [InlineKeyboardButton("📋 Слова на обучении", callback_data="menu_learning")],
        [InlineKeyboardButton("📊 Статистика", callback_data="menu_stats")],
        [InlineKeyboardButton("➕ Добавить слово", callback_data="menu_add")],
        [InlineKeyboardButton("🔧 Дополнительно", callback_data="menu_more")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_more_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔄 Повторение", callback_data="menu_review")],
        [InlineKeyboardButton("📝 Практика", callback_data="menu_practice")],
        [InlineKeyboardButton("🗣️ Диалог", callback_data="menu_dialogue")],
        [InlineKeyboardButton("🔴 Слабые слова", callback_data="menu_weak")],
        [InlineKeyboardButton("📈 Прогресс", callback_data="menu_progress")],
        [InlineKeyboardButton("🔄 Сброс learning", callback_data="menu_reset_learning")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_learn_keyboard(uid, idx):
    keyboard = [
        [InlineKeyboardButton("💡 Подсказка", callback_data=f"hint_{uid}_{idx}")],
        [
            InlineKeyboardButton("❌ Пропустить", callback_data=f"skip_{uid}_{idx}"),
            InlineKeyboardButton("⏹️ Закончить", callback_data=f"stop_{uid}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# === ЕЖЕДНЕВНАЯ РАССЫЛКА ===
async def send_daily_tasks():
    bot = Bot(token=os.environ.get('TELEGRAM_BOT_TOKEN'))
    users = load_user_data()
    for uid_str, data in users.items():
        if data.get("receives_daily", True):
            try:
                practice_words = get_words_for_practice(5)
                if practice_words:
                    sentences = generate_practice_sentences(practice_words, 5)
                    practice_text = f"📝 Ежедневная практика (5 предложений)\n\n{sentences}\n\nПереведи и отправь мне!"
                else:
                    practice_text = "📚 Выучи больше слов для практики!"
                new_count = len(get_words_by_status("new"))
                learning_count = len(get_words_by_status("learning"))
                due_count = len([w for w in get_words_by_statuses(["review_1", "review_2", "review_3"]) 
                                if w.get("next_review_date") and datetime.fromisoformat(w["next_review_date"]) <= datetime.now()])
                
                reminder = ""
                if new_count > 0:
                    reminder += f"\n📌 Осталось новых слов: {new_count}. Введи /daily_words"
                if learning_count > 0:
                    reminder += f"\n📖 {learning_count} слов ждут тренировки! Введи /learn"
                if due_count > 0:
                    reminder += f"\n🔄 {due_count} слов ждут повторения! Введи /learn"
                
                await bot.send_message(
                    chat_id=int(uid_str),
                    text=practice_text + reminder
                )
            except Exception as e:
                logging.error(f"Ошибка: {e}")

# === КОМАНДЫ ===
async def start(update, context):
    uid = str(update.effective_user.id)
    if uid not in user_data:
        user_data[uid] = {"receives_daily": True}
        save_user_data(user_data)
    
    text = "👋 *Привет! Я — система изучения английского для IT!*\n\n"
    text += "📊 *Твой прогресс:*\n"
    text += f"📥 Новых: {len(get_words_by_status('new'))}\n"
    text += f"📖 На обучении: {len(get_words_by_status('learning'))}\n"
    text += f"🔄 На повторении: {len(get_words_by_statuses(['review_1', 'review_2', 'review_3']))}\n"
    text += f"✅ Выучено: {len(get_words_by_status('mastered'))}\n\n"
    text += "Выбери действие:"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = update.effective_user.id
    
    if data == "menu_daily":
        await daily_words(update, context)
    elif data == "menu_learn":
        await learn(update, context)
    elif data == "menu_learning":
        await show_learning_words(update, context)
    elif data == "menu_stats":
        await stats(update, context)
    elif data == "menu_add":
        await query.edit_message_text(
            "➕ *Добавить слово*\n\n"
            "Отправь сообщение в формате:\n"
            "`Добавь слово to pivot — разворот #agile`\n\n"
            "Тема (#agile) — опционально.",
            parse_mode="Markdown",
            reply_markup=get_more_keyboard()
        )
    elif data == "menu_more":
        await query.edit_message_text("🔧 *Дополнительные функции:*", parse_mode="Markdown", reply_markup=get_more_keyboard())
    elif data == "menu_back":
        await start(update, context)
    elif data == "menu_review":
        await review(update, context)
    elif data == "menu_practice":
        await practice(update, context)
    elif data == "menu_dialogue":
        await dialogue(update, context)
    elif data == "menu_weak":
        await weak_words(update, context)
    elif data == "menu_progress":
        await progress(update, context)
    elif data == "menu_reset_learning":
        await reset_learning(update, context)
    elif data.startswith("hint_"):
        await handle_hint(update, context)
    elif data.startswith("skip_"):
        await handle_skip(update, context)
    elif data.startswith("stop_"):
        await handle_stop(update, context)
    elif data.startswith("learn_repeat_errors_"):
        await repeat_errors(update, context)
    elif data.startswith("learn_done_"):
        await finish_learn_session(update, uid)

# === ОБРАБОТЧИКИ КНОПОК ОБУЧЕНИЯ ===
async def handle_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    
    if uid not in sessions:
        await query.edit_message_text("Сессия завершена. Начни заново через /learn")
        return
    
    session = sessions[uid]
    idx = session.get("index", 0)
    cards = session.get("cards", [])
    if idx >= len(cards):
        await query.answer("Это слово уже пройдено")
        return
    
    card = cards[idx]
    direction = session.get("direction", "ru_to_en")
    hint = get_hint(card["word"], direction)
    
    await query.message.reply_text(hint)
    await query.answer("💡 Подсказка отправлена!")

async def handle_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    
    if uid not in sessions:
        await query.edit_message_text("Сессия завершена. Начни заново через /learn")
        return
    
    session = sessions[uid]
    idx = session.get("index", 0)
    cards = session.get("cards", [])
    if idx >= len(cards):
        await query.answer("Это слово уже пройдено")
        return
    
    card = cards[idx]
    word_text = card["word"].split(" — ")[0]
    
    session["results"].append({
        "word": word_text,
        "correct": False,
        "user_answer": "⏭️ пропущено",
        "correct_answer": session.get("current_correct", "")
    })
    session["errors"].append(word_text)
    session["index"] += 1
    session["waiting_for_answer"] = False
    
    await query.answer("⏭️ Пропущено!")
    
    try:
        await query.message.delete()
    except:
        pass
    
    await query.message.reply_text("⏭️ Пропущено. Перехожу к следующему...")
    await show_learn_card(update, uid)

async def handle_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    await query.answer("⏹️ Сессия завершена")
    await finish_learn_session(update, uid)

# === КОМАНДА: СЛОВА В LEARNING ===
async def show_learning_words(update, context):
    learning_words = get_learning_words()
    if not learning_words:
        text = "📋 У тебя нет слов в обучении. Возьми новые через /daily_words"
    else:
        text = "📋 *Слова, которые ты сейчас учишь:*\n\n"
        for i, w in enumerate(learning_words, 1):
            text += f"{i}. {w['word']}\n"
        text += f"\n📊 Всего: {len(learning_words)} слов"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

# === КОМАНДА: ВЗЯТЬ НОВЫЕ СЛОВА ===
async def daily_words(update, context):
    today = datetime.now().date()
    daily_taken = [w for w in get_words_by_status("learning") 
                   if w.get("learned_at") and datetime.fromisoformat(w["learned_at"]).date() == today]
    
    if len(daily_taken) >= 10:
        text = "📚 Сегодня ты уже взял 10 слов для обучения!\nЗавтра будет новая порция. А пока потренируй уже взятые слова через `/learn`."
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        return
    
    new_words = get_words_by_status("new")
    if not new_words:
        text = "🎉 Поздравляю! У тебя нет новых слов. Добавь новые через `Добавь слово X — Y`"
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        return
    
    available = min(5, 10 - len(daily_taken), len(new_words))
    selected = random.sample(new_words, available)
    
    for w in selected:
        w["status"] = "learning"
        w["learned_at"] = datetime.now().isoformat()
    save_words(words)
    
    if update.callback_query:
        target = update.callback_query.message
    else:
        target = update.message
    
    await target.reply_text("🧠 Генерирую примеры предложений для новых слов... Подожди немного.")
    
    examples = generate_examples_for_words(selected)
    
    text = f"📚 *Ты взял {len(selected)} новых слов для обучения:*\n\n"
    for i, w in enumerate(selected, 1):
        text += f"{i}. {w['word']}\n"
    text += f"\n📊 Сегодня уже взято: {len(daily_taken) + len(selected)}/10 слов.\n"
    
    if examples:
        text += f"\n📝 *Примеры использования:*\n\n{examples}"
    else:
        text += f"\n⚠️ Не удалось сгенерировать примеры. Попробуй позже."
    
    text += f"\n\n✍️ Теперь переходи в `/learn` чтобы начать тренировку!"
    
    parts = split_text(text, 4000)
    for part in parts:
        await target.reply_text(part, parse_mode="Markdown")
    
    await target.reply_text("Выбери действие:", reply_markup=get_main_keyboard())

# === КОМАНДА: СТАТИСТИКА ===
async def stats(update, context):
    new_count = len(get_words_by_status("new"))
    learning_count = len(get_words_by_status("learning"))
    review_1 = len(get_words_by_status("review_1"))
    review_2 = len(get_words_by_status("review_2"))
    review_3 = len(get_words_by_status("review_3"))
    mastered_count = len(get_words_by_status("mastered"))
    
    text = f"📊 *Твоя статистика*\n\n"
    text += f"📥 Новых: {new_count}\n"
    text += f"📖 На обучении: {learning_count}\n"
    text += f"🔄 Повторение (3 дня): {review_1}\n"
    text += f"🔄 Повторение (7 дней): {review_2}\n"
    text += f"🔄 Повторение (14 дней): {review_3}\n"
    text += f"✅ Выучено: {mastered_count}\n"
    text += f"📚 Всего слов: {len(words)}"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

# === КОМАНДА: ПРОГРЕСС ===
async def progress(update, context):
    learned_words = get_words_by_statuses(["learning", "review_1", "review_2", "review_3", "mastered"])
    if not learned_words:
        text = "📈 У тебя пока нет слов в обучении. Начни с /daily_words!"
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_more_keyboard())
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_more_keyboard())
        return
    
    daily_counts = defaultdict(int)
    for w in learned_words:
        if w.get("learned_at"):
            date = w["learned_at"][:10]
            daily_counts[date] += 1
    
    if not daily_counts:
        text = "Нет данных для прогресса."
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_more_keyboard())
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_more_keyboard())
        return
    
    sorted_dates = sorted(daily_counts.keys())[-30:]
    graph = "📈 *Прогресс по дням (последние 30 дней)*\n\n"
    for date in sorted_dates:
        count = daily_counts[date]
        bar = "█" * min(count, 20) + " " * (20 - min(count, 20))
        graph += f"{date}: {bar} {count}\n"
    
    total = sum(daily_counts.values())
    avg = total / len(daily_counts) if daily_counts else 0
    graph += f"\n📊 Всего слов в работе: {total}\n"
    graph += f"📊 В среднем в день: {avg:.1f}"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(graph, parse_mode="Markdown", reply_markup=get_more_keyboard())
    else:
        await update.message.reply_text(graph, parse_mode="Markdown", reply_markup=get_more_keyboard())

# === КОМАНДА: СЛАБЫЕ СЛОВА ===
async def weak_words(update, context):
    error_words = [w for w in words if w.get("error_count", 0) > 0]
    if not error_words:
        text = "🔴 Отлично! У тебя нет слов, в которых ты часто ошибаешься."
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_more_keyboard())
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_more_keyboard())
        return
    
    sorted_words = sorted(error_words, key=lambda x: x.get("error_count", 0), reverse=True)[:10]
    text = "🔴 *Топ-10 самых проблемных слов:*\n\n"
    for i, w in enumerate(sorted_words, 1):
        text += f"{i}. {w['word']} — ошибок: {w.get('error_count', 0)}\n"
    text += "\nИспользуй `/status слово — learning` чтобы вернуть слово в обучение."
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_more_keyboard())
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_more_keyboard())

# === КОМАНДА: СБРОС LEARNING ===
async def reset_learning(update, context):
    learning_words = get_words_by_status("learning")
    if not learning_words:
        text = "📭 Нет слов в статусе learning.\n\nВсе слова уже в new или на других этапах."
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        return
    
    count = 0
    for w in words:
        if w.get("status") == "learning":
            w["status"] = "new"
            w["learned_at"] = None
            count += 1
    save_words(words)
    
    text = f"✅ *{count} слов возвращены в статус NEW!*\n\n"
    text += f"Теперь ты можешь взять их заново через `/daily_words`.\n\n"
    text += f"💡 *Совет:* не бери слишком много слов за раз — 5-10 в день оптимально."
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

# === КОМАНДА: ТРЕНИРОВКА (/learn) ===
async def learn(update, context):
    uid = update.effective_user.id
    if uid in sessions:
        if update.callback_query:
            await update.callback_query.edit_message_text("У тебя уже есть активная сессия.", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text("У тебя уже есть активная сессия.", reply_markup=get_main_keyboard())
        return

    cards = get_words_for_learn(10)
    if not cards:
        text = "📚 Нет слов для тренировки.\nСначала возьми новые слова через `/daily_words`"
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        return

    learning_count = len([w for w in cards if w.get("status") == "learning"])
    review_count = len([w for w in cards if w.get("status") in ["review_1", "review_2", "review_3"]])
    
    msg = f"🧠 *Тренировка!* Будет {len(cards)} слов.\n"
    if learning_count > 0:
        msg += f"📖 Новых: {learning_count}\n"
    if review_count > 0:
        msg += f"🔄 Повторений: {review_count}\n"
    msg += "\n✍️ Пиши перевод вручную. Бот проверит и поправит."
    
    sessions[uid] = {
        "mode": "learn",
        "cards": cards,
        "index": 0,
        "direction": "ru_to_en",
        "results": [],
        "errors": [],
        "waiting_for_answer": True,
        "current_correct": "",
        "current_message_id": None
    }
    
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())
    
    await show_learn_card(update, uid)

async def show_learn_card(update, uid):
    if uid not in sessions:
        return
    
    session = sessions[uid]
    idx = session["index"]
    cards = session["cards"]
    direction = session["direction"]
    
    if idx >= len(cards):
        if direction == "ru_to_en":
            session["direction"] = "en_to_ru"
            session["index"] = 0
            if update.callback_query:
                await update.callback_query.message.reply_text("🔄 Теперь АНГЛИЙСКИЙ → РУССКИЙ")
            else:
                await update.message.reply_text("🔄 Теперь АНГЛИЙСКИЙ → РУССКИЙ")
            await show_learn_card(update, uid)
            return
        else:
            await finish_learn_session(update, uid)
            return
    
    card = cards[idx]
    word = card["word"]
    word_en = word.split(" — ")[0]
    word_ru = word.split(" — ")[1]
    status = card.get("status", "")
    
    status_emoji = {
        "learning": "📖",
        "review_1": "🔄 (3д)",
        "review_2": "🔄 (7д)",
        "review_3": "🔄 (14д)"
    }.get(status, "")
    
    if direction == "ru_to_en":
        text = f"{status_emoji} *{word_ru}*  ({idx+1}/{len(cards)})\n\n✍️ Напиши перевод на английский:"
        session["current_correct"] = word_en
    else:
        text = f"{status_emoji} *{word_en}*  ({idx+1}/{len(cards)})\n\n✍️ Напиши перевод на русский:"
        session["current_correct"] = word_ru
    
    session["waiting_for_answer"] = True
    
    keyboard = get_learn_keyboard(uid, idx)
    
    if update.callback_query:
        msg = await update.callback_query.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        msg = await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
    
    session["current_message_id"] = msg.message_id

async def handle_learn_answer(update, context):
    uid = update.effective_user.id
    text = update.message.text
    
    if uid not in sessions:
        return
    
    session = sessions[uid]
    if not session.get("waiting_for_answer", False):
        return
    
    idx = session["index"]
    cards = session["cards"]
    if idx >= len(cards):
        return
    
    card = cards[idx]
    word_text = card["word"].split(" — ")[0]
    correct_answer = session["current_correct"]
    user_answer = text.strip()
    
    feedback = check_translation(user_answer, correct_answer)
    is_correct = "✅" in feedback or "правильно" in feedback.lower()
    
    session["results"].append({
        "word": word_text,
        "correct": is_correct,
        "user_answer": user_answer,
        "correct_answer": correct_answer
    })
    
    if not is_correct:
        session["errors"].append(word_text)
        w = get_word_entry_by_text(word_text)
        if w:
            w["error_count"] = w.get("error_count", 0) + 1
            save_words(words)
    
    await update.message.reply_text(feedback)
    
    session["index"] += 1
    session["waiting_for_answer"] = False
    
    try:
        if session.get("current_message_id"):
            await update.message.delete()
    except:
        pass
    
    await show_learn_card(update, uid)

async def finish_learn_session(update, uid):
    if uid not in sessions:
        if update.callback_query:
            await update.callback_query.edit_message_text("Сессия завершена. Выбери действие:", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text("Сессия завершена. Выбери действие:", reply_markup=get_main_keyboard())
        return
    
    session = sessions[uid]
    results = session.get("results", [])
    errors = session.get("errors", [])
    
    total = len(results)
    correct = len([r for r in results if r["correct"]])
    wrong = total - correct
    
    text = f"📊 *Результаты тренировки*\n\n"
    text += f"✅ Правильных: {correct}\n"
    text += f"❌ Ошибок: {wrong}\n"
    
    learned_words = []
    for r in results:
        if r["correct"]:
            learned_words.append(r["word"])
    
    if learned_words:
        for word_text in learned_words:
            w = get_word_entry_by_text(word_text)
            if w:
                current_status = w.get("status")
                if current_status == "learning":
                    update_word_status(word_text, "review_1", True)
                elif current_status in ["review_1", "review_2", "review_3"]:
                    next_status = NEXT_STATUS.get(current_status, "mastered")
                    update_word_status(word_text, next_status, True)
        text += f"\n\n✅ Выучено слов: {len(learned_words)} (перешли на следующий этап)"
    
    if errors:
        text += f"\n\n🔄 *Слова с ошибками (остались в обучении):*\n"
        error_list = list(set(errors))[:10]
        for word in error_list:
            text += f"• {word}\n"
        if len(set(errors)) > 10:
            text += f"... и ещё {len(set(errors)) - 10} слов\n"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Да, повторить ошибки", callback_data=f"learn_repeat_errors_{uid}")],
            [InlineKeyboardButton("✅ Закончить", callback_data=f"learn_done_{uid}")]
        ])
        
        session["finished"] = True
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
        return
    
    text += "\n\n🎉 Отличная работа! Ты прошёл все слова!"
    del sessions[uid]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def repeat_errors(update, context):
    query = update.callback_query
    uid = update.effective_user.id
    
    if uid not in sessions:
        await query.edit_message_text("Сессия завершена.", reply_markup=get_main_keyboard())
        return
    
    session = sessions[uid]
    error_words = list(set(session.get("errors", [])))
    if not error_words:
        await query.edit_message_text("Нет ошибок для повторения.", reply_markup=get_main_keyboard())
        return
    
    error_cards = []
    for word_text in error_words:
        w = get_word_entry_by_text(word_text)
        if w:
            error_cards.append(w)
    
    if not error_cards:
        await query.edit_message_text("Слова не найдены.", reply_markup=get_main_keyboard())
        return
    
    sessions[uid] = {
        "mode": "learn",
        "cards": error_cards,
        "index": 0,
        "direction": "ru_to_en",
        "results": [],
        "errors": [],
        "waiting_for_answer": True,
        "current_correct": "",
        "current_message_id": None,
        "is_repeat": True
    }
    
    await query.edit_message_text("🔄 *Повторяем только слова с ошибками!*\n\nБудь внимателен!", parse_mode="Markdown")
    await show_learn_card(update, uid)

# === КОМАНДА: ПОВТОРЕНИЕ (/review) ===
async def review(update, context):
    await learn(update, context)

# === КОМАНДА: ПРАКТИКА ===
async def practice(update, context):
    count = 5
    difficulty = "medium"
    if context.args and len(context.args) > 0:
        try:
            if context.args[0] in ["easy", "medium", "hard"]:
                difficulty = context.args[0]
                if len(context.args) > 1:
                    count = int(context.args[1])
            else:
                count = int(context.args[0])
        except ValueError:
            pass
    
    if update.callback_query:
        target = update.callback_query.message
    else:
        target = update.message
    
    words_for_practice = get_words_for_practice(count, difficulty)
    if not words_for_practice:
        text = f"📝 Нет выученных слов для практики (сложность: {difficulty}).\n\nСначала выучи слова через /learn, чтобы они перешли в статус review или mastered."
        await target.reply_text(text, parse_mode="Markdown", reply_markup=get_more_keyboard())
        return
    
    await target.reply_text("⏳ Генерирую задания...")
    cloze_text = generate_cloze(words_for_practice, count)
    
    parts = split_text(cloze_text, 4000)
    
    for part in parts:
        await target.reply_text(f"📝 *Практика (заполни пропуски)*\n\n{part}", parse_mode="Markdown")
    
    await target.reply_text("✍️ Напиши свои ответы, я проверю!", reply_markup=get_more_keyboard())

# === КОМАНДА: ДИАЛОГ ===
async def dialogue(update, context):
    if update.callback_query:
        target = update.callback_query.message
    else:
        target = update.message
    
    available = get_words_by_status("mastered") + get_words_by_statuses(["review_1", "review_2", "review_3"])
    if len(available) < 5:
        text = f"🗣️ Недостаточно выученных слов для диалога (нужно минимум 5, а у тебя {len(available)}).\n\nВыучи больше слов через /learn."
        await target.reply_text(text, parse_mode="Markdown", reply_markup=get_more_keyboard())
        return
    
    selected = random.sample(available, min(10, len(available)))
    await target.reply_text("🗣️ Генерирую диалог на IT-тему...")
    dialogue_text = generate_dialogue(selected, topic="general")
    
    parts = split_text(dialogue_text, 4000)
    for part in parts:
        await target.reply_text(f"🗣️ *Диалог для практики*\n\n{part}", parse_mode="Markdown")
    
    await target.reply_text("📌 Прочитай диалог вслух, попробуй использовать эти слова в своей речи.", reply_markup=get_more_keyboard())

# === КОМАНДА: СТАТУС ===
async def set_status(update, context):
    text = update.message.text
    result = detect_status_command(text)
    if not result:
        await update.message.reply_text(
            "Используй формат: `/status слово — status`\n"
            "Доступные статусы: new, learning, review_1, review_2, review_3, mastered\n\n"
            "Пример: `/status pivot — learning`",
            parse_mode="Markdown"
        )
        return

    word_text, new_status = result
    valid_statuses = ["new", "learning", "review_1", "review_2", "review_3", "mastered"]
    if new_status not in valid_statuses:
        await update.message.reply_text(f"❌ Неверный статус. Доступны: {', '.join(valid_statuses)}")
        return

    word_entry = get_word_entry_by_text(word_text)
    if not word_entry:
        await update.message.reply_text(f"❌ Слово '{word_text}' не найдено в словаре.")
        return

    old_status = word_entry.get("status", "unknown")
    update_word_status(word_text, new_status, set_review_date=True)

    status_emojis = {
        "new": "🆕",
        "learning": "📖",
        "review_1": "🔄1",
        "review_2": "🔄2",
        "review_3": "🔄3",
        "mastered": "✅"
    }

    await update.message.reply_text(
        f"✅ Статус слова обновлён!\n\n"
        f"{word_entry['word']}\n"
        f"{status_emojis.get(old_status, '❓')} → {status_emojis.get(new_status, '❓')}",
        parse_mode="Markdown"
    )

# === КОМАНДА: ОСТАНОВИТЬ СЕССИЮ ===
async def stop(update, context):
    uid = update.effective_user.id
    if uid in sessions:
        del sessions[uid]
        await update.message.reply_text("⏹️ Сессия остановлена.", reply_markup=get_main_keyboard())
    else:
        await update.message.reply_text("ℹ️ Нет активной сессии.", reply_markup=get_main_keyboard())

# === КОМАНДА: ВКЛ/ВЫКЛ РАССЫЛКУ ===
async def toggle(update, context):
    uid = str(update.effective_user.id)
    if uid not in user_data:
        user_data[uid] = {}
    cmd = update.message.text.lower()
    if "on" in cmd:
        user_data[uid]["receives_daily"] = True
        save_user_data(user_data)
        await update.message.reply_text("✅ Ежедневная рассылка включена.", reply_markup=get_main_keyboard())
    else:
        user_data[uid]["receives_daily"] = False
        save_user_data(user_data)
        await update.message.reply_text("❌ Ежедневная рассылка выключена.", reply_markup=get_main_keyboard())

# === ОСНОВНОЙ ОБРАБОТЧИК ===
async def handle(update, context):
    uid = update.effective_user.id
    text = update.message.text
    
    if uid in sessions and sessions[uid].get("mode") == "learn" and sessions[uid].get("waiting_for_answer", False):
        await handle_learn_answer(update, context)
        return
    
    add = detect_add_word(text)
    if add:
        word, trans, topic = add
        full_entry = f"{word} — {trans}"
        existing = get_word_entry_by_text(word)
        if existing:
            await update.message.reply_text(f"⚠️ Слово уже есть в словаре!\n{existing['word']}")
            return

        new_word = {
            "word": full_entry,
            "status": "new",
            "added_at": datetime.now().isoformat(),
            "learned_at": None,
            "review_count": 0,
            "error_count": 0,
            "interval": 1,
            "next_review_date": None,
            "difficulty": "medium",
            "topics": [topic] if topic else []
        }
        words.append(new_word)
        save_words(words)

        await update.message.reply_text(
            f"✅ Слово добавлено!\n{full_entry}\n"
            + (f"📌 Тема: #{topic}\n" if topic else "")
            + f"\n📚 Теперь в словаре {len(words)} слов.\n"
            f"💡 Введи /daily_words чтобы начать изучение.",
            parse_mode="Markdown"
        )
        return
    
    translation = find_translation(text)
    if translation:
        en, ru, full = translation
        await update.message.reply_text(
            f"📖 *{text}*\n\n"
            f"🇬🇧 EN: {en}\n"
            f"🇷🇺 RU: {ru}",
            parse_mode="Markdown"
        )
        return
    
    if len(text.split('\n')) >= 2:
        await update.message.chat.send_action(action="typing")
        await update.message.reply_text("🔍 Проверяю переводы...")
        result = check_sentences(text)
        await update.message.reply_text(result, reply_markup=get_main_keyboard())
        return
    
    await update.message.chat.send_action(action="typing")
    resp = ask_deepseek(uid, text)
    await update.message.reply_text(resp, reply_markup=get_main_keyboard())

# === ЗАПУСК ===
async def post_init(application):
    scheduler.start()

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        logging.error("❌ Токен не найден! Проверь .env")
        return

    proxy_url = os.environ.get('PROXY_URL')
    request_kwargs = {
        "connection_pool_size": 8,
        "read_timeout": 30,
        "write_timeout": 30,
        "connect_timeout": 30,
    }
    if proxy_url:
        request = HTTPXRequest(proxy=proxy_url, **request_kwargs)
        logging.info(f"✅ Используется прокси: {proxy_url}")
    else:
        request = HTTPXRequest(**request_kwargs)

    app = Application.builder().token(token).request(request).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("progress", progress))
    app.add_handler(CommandHandler("weak", weak_words))
    app.add_handler(CommandHandler("daily_words", daily_words))
    app.add_handler(CommandHandler("learn", learn))
    app.add_handler(CommandHandler("review", review))
    app.add_handler(CommandHandler("practice", practice))
    app.add_handler(CommandHandler("dialogue", dialogue))
    app.add_handler(CommandHandler("learning", show_learning_words))
    app.add_handler(CommandHandler("status", set_status))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("on", toggle))
    app.add_handler(CommandHandler("off", toggle))
    app.add_handler(CommandHandler("reset_learning", reset_learning))

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    scheduler.add_job(send_daily_tasks, CronTrigger(hour=9, minute=0), id='daily')

    print("✅ Бот запущен!")
    print("📚 /daily_words — взять 5 новых слов с примерами")
    print("🧠 /learn — тренировка с ручным вводом и кнопками")
    print("📝 /practice — практика Cloze")
    print("🗣️ /dialogue — диалог на IT-тему")

    app.run_polling()

if __name__ == "__main__":
    main()
