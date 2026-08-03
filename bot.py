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

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def split_text(text, max_length=4000):
    """Разбивает длинный текст на части для отправки в Telegram"""
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

def get_word_entry_by_text(word_text):
    for w in words:
        if w["word"].split(" — ")[0].lower() == word_text.lower():
            return w
    return None

def update_word_status_by_text(word_text, new_status):
    for w in words:
        if w["word"].split(" — ")[0].lower() == word_text.lower():
            w["status"] = new_status
            if new_status == "learned":
                w["learned_at"] = datetime.now().isoformat()
                w["next_review_date"] = (datetime.now() + timedelta(days=1)).isoformat()
                w["interval"] = 1
            elif new_status == "review":
                w["review_count"] = w.get("review_count", 0) + 1
                w["last_reviewed"] = datetime.now().isoformat()
            save_words(words)
            return True
    return False

def update_word_after_review(word_text, correct):
    w = get_word_entry_by_text(word_text)
    if not w:
        return
    if correct:
        current_interval = w.get("interval", 1)
        if current_interval == 1:
            new_interval = 3
        elif current_interval == 3:
            new_interval = 7
        elif current_interval == 7:
            new_interval = 14
        elif current_interval == 14:
            new_interval = 30
        else:
            new_interval = 30
        w["interval"] = new_interval
        w["next_review_date"] = (datetime.now() + timedelta(days=new_interval)).isoformat()
    else:
        w["interval"] = 1
        w["next_review_date"] = (datetime.now() + timedelta(days=1)).isoformat()
        w["error_count"] = w.get("error_count", 0) + 1
    save_words(words)

def get_topic_from_text(text):
    match = re.search(r'#(\w+)', text)
    if match:
        return match.group(1).lower()
    return None

def get_words_by_topic(topic):
    return [w for w in words if topic in w.get("topics", [])]

def get_daily_words(count=10, topic=None):
    if topic:
        pool = get_words_by_topic(topic)
        pool = [w for w in pool if w.get("status") == "new"]
    else:
        pool = get_words_by_status("new")
    if len(pool) < count:
        count = len(pool)
    if count == 0:
        return []
    return random.sample(pool, count)

def get_words_for_learn(limit=10, topic=None):
    if topic:
        pool = get_words_by_topic(topic)
        learning = [w for w in pool if w.get("status") == "learning"]
        new = [w for w in pool if w.get("status") == "new"]
    else:
        learning = get_words_by_status("learning")
        new = get_words_by_status("new")
    available = learning + new
    if not available:
        return []
    return random.sample(available, min(limit, len(available)))

def get_words_for_review(limit=15):
    now = datetime.now()
    review_pool = get_words_by_status("review")
    learned_pool = get_words_by_status("learned")
    all_words = review_pool + learned_pool
    due_words = [w for w in all_words if w.get("next_review_date") and datetime.fromisoformat(w["next_review_date"]) <= now]
    if len(due_words) < limit:
        learned_sorted = sorted([w for w in learned_pool if w not in due_words], key=lambda x: x.get("learned_at", "1970-01-01"))
        due_words += learned_sorted[:limit - len(due_words)]
    if not due_words:
        return []
    return random.sample(due_words, min(limit, len(due_words)))

def get_words_for_practice(limit=10, difficulty=None):
    learned = get_words_by_status("learned")
    review = get_words_by_status("review")
    available = learned + review
    if difficulty:
        available = [w for w in available if w.get("difficulty", "medium") == difficulty]
    if not available:
        return []
    return random.sample(available, min(limit, len(available)))

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
    Формат:
    Диалог:
    A: ...
    B: ...
    ...
    Перевод:
    ...
    Выделенные слова: ...
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

async def send_daily_tasks():
    bot = Bot(token=os.environ.get('TELEGRAM_BOT_TOKEN'))
    users = load_user_data()
    for uid_str, data in users.items():
        if data.get("receives_daily", True):
            try:
                practice_words = get_words_for_practice(5)
                if practice_words:
                    sentences = generate_practice_sentences(practice_words, 5)
                    practice_text = f"ЕЖЕДНЕВНАЯ ПРАКТИКА (5 предложений)\n\n{sentences}\n\nПереведи и отправь мне!"
                else:
                    practice_text = "Выучи больше слов, чтобы начать практику с предложениями!"
                new_count = len(get_words_by_status("new"))
                due_count = len([w for w in get_words_by_status("learned") if w.get("next_review_date") and datetime.fromisoformat(w["next_review_date"]) <= datetime.now()])
                if new_count > 0:
                    reminder = f"\n\n📌 На сегодня осталось {new_count} новых слов. Введи /daily_words чтобы взять 5 слов для изучения."
                else:
                    reminder = "\n\n🎉 Все слова изучены! Добавь новые через `Добавь слово X — Y`"
                if due_count > 0:
                    reminder += f"\n🔄 {due_count} слов ждут повторения! Введи /review."
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
    new_count = len(get_words_by_status("new"))
    learning_count = len(get_words_by_status("learning"))
    learned_count = len(get_words_by_status("learned"))
    review_count = len(get_words_by_status("review"))
    await update.message.reply_text(
        f"Привет! Я — система изучения английского для IT!\n\n"
        f"Твой прогресс:\n"
        f"Совсем не знаю: {new_count}\n"
        f"На обучении: {learning_count}\n"
        f"Выучил: {learned_count}\n"
        f"Активно использую: {review_count}\n\n"
        "Команды:\n"
        "/daily_words [тема] — взять 5 новых слов с примерами (можно указать тему, например #agile)\n"
        "/learn [тема] — обучение с кнопками (можно указать тему)\n"
        "/review — повторение (15 слов по интервальному графику)\n"
        "/practice [easy|medium|hard] — практика предложений (можно выбрать сложность)\n"
        "/dialogue — диалог на IT-тему с использованием выученных слов\n"
        "/weak — показать топ-10 слов, в которых ты ошибался\n"
        "/progress — показать прогресс по дням\n"
        "/stats — статистика\n"
        "/status слово — new/learning/learned/review — изменить статус слова\n"
        "/on — включить ежедневную рассылку\n"
        "/off — выключить ежедневную рассылку\n\n"
        "Добавить слово: Добавь слово X — Y #тема\n"
        "Пример: Добавь слово pivot — разворот #agile"
    )

async def stats(update, context):
    new_count = len(get_words_by_status("new"))
    learning_count = len(get_words_by_status("learning"))
    learned_count = len(get_words_by_status("learned"))
    review_count = len(get_words_by_status("review"))
    uid = str(update.effective_user.id)
    status = "Включена" if user_data.get(uid, {}).get("receives_daily", True) else "Выключена"
    await update.message.reply_text(
        f"Твоя статистика\n\n"
        f"Совсем не знаю: {new_count}\n"
        f"На обучении: {learning_count}\n"
        f"Выучил: {learned_count}\n"
        f"Активно использую: {review_count}\n"
        f"Всего слов: {len(words)}\n"
        f"Рассылка: {status}"
    )

async def progress(update, context):
    learned_words = get_words_by_status("learned")
    if not learned_words:
        await update.message.reply_text("У тебя пока нет выученных слов. Начни с /daily_words!")
        return
    daily_counts = defaultdict(int)
    for w in learned_words:
        if w.get("learned_at"):
            date = w["learned_at"][:10]
            daily_counts[date] += 1
    if not daily_counts:
        await update.message.reply_text("Нет данных для прогресса.")
        return
    sorted_dates = sorted(daily_counts.keys())[-30:]
    if not sorted_dates:
        await update.message.reply_text("Недостаточно данных.")
        return
    max_count = max(daily_counts.values())
    graph = "📈 *Прогресс по дням (последние 30 дней)*\n\n"
    for date in sorted_dates:
        count = daily_counts[date]
        bar = "█" * min(count, 20) + " " * (20 - min(count, 20))
        graph += f"{date}: {bar} {count}\n"
    total = sum(daily_counts.values())
    avg = total / len(daily_counts) if daily_counts else 0
    graph += f"\n📊 Всего выучено за 30 дней: {total}\n"
    graph += f"📊 В среднем в день: {avg:.1f}"
    await update.message.reply_text(graph, parse_mode="Markdown")

async def weak_words(update, context):
    error_words = [w for w in words if w.get("error_count", 0) > 0]
    if not error_words:
        await update.message.reply_text("Отлично! У тебя нет слов, в которых ты часто ошибаешься. Продолжай в том же духе!")
        return
    sorted_words = sorted(error_words, key=lambda x: x.get("error_count", 0), reverse=True)[:10]
    text = "🔴 *Топ-10 самых проблемных слов:*\n\n"
    for i, w in enumerate(sorted_words, 1):
        text += f"{i}. {w['word']} — ошибок: {w.get('error_count', 0)}\n"
    text += "\nЧтобы переучить эти слова, используй `/learn` или `/status слово — learning`"
    await update.message.reply_text(text, parse_mode="Markdown")

async def daily_words(update, context):
    count = 5  # ← УМЕНЬШЕНО ДО 5
    topic = None
    if context.args and len(context.args) > 0:
        try:
            if context.args[0].startswith('#'):
                topic = context.args[0][1:].lower()
            else:
                count = int(context.args[0])
                if count < 1:
                    count = 1
                if count > 10:
                    count = 10
                if len(context.args) > 1 and context.args[1].startswith('#'):
                    topic = context.args[1][1:].lower()
        except ValueError:
            if context.args[0].startswith('#'):
                topic = context.args[0][1:].lower()
            else:
                await update.message.reply_text("Укажи число или тему, например: /daily_words 5 #agile")
                return

    daily = get_daily_words(count, topic)
    if not daily:
        if topic:
            await update.message.reply_text(f"Нет новых слов по теме #{topic}. Добавь слова с этим тегом или попробуй другую тему.")
        else:
            await update.message.reply_text("Поздравляю! Ты уже изучил все слова из категории New!\nДобавь новые слова через `Добавь слово X — Y`")
        return

    word_list = [w['word'].split(' — ')[0] for w in daily]

    await update.message.reply_text("🧠 Генерирую примеры предложений для слов... Подожди немного.")

    prompt = f"""
    Для каждого из следующих слов напиши ровно 2 примера предложений на английском языке с переводом на русский.
    Предложения должны быть из IT-сферы (Product Management, аналитика, разработка, менеджмент).
    Слова: {', '.join(word_list)}

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
        examples = response.choices[0].message.content

        if not examples or len(examples.strip()) < 10:
            raise ValueError("Ответ от DeepSeek пустой или слишком короткий")

        header = f"📚 Твои {len(daily)} новых слов" + (f" по теме #{topic}" if topic else "") + " с примерами:\n\n"
        full_text = header + examples

        parts = split_text(full_text, 4000)

        for part in parts:
            await update.message.reply_text(part, parse_mode="Markdown")

        await update.message.reply_text("✍️ Теперь выучи эти слова через `/learn` (карточки с кнопками).")

    except Exception as e:
        logging.error(f"Ошибка генерации примеров: {e}")
        import traceback
        traceback.print_exc()

        text = f"⚠️ Не удалось сгенерировать примеры для этих слов. Вот список слов:\n\n"
        for i, w in enumerate(daily, 1):
            text += f"{i}. {w['word']}\n"
        text += f"\n❌ Ошибка: {str(e)[:200]}"
        await update.message.reply_text(text)

# === РЕЖИМ ОБУЧЕНИЯ С КНОПКАМИ ===

async def learn(update, context):
    uid = update.effective_user.id
    if uid in sessions:
        await update.message.reply_text("У тебя уже есть активная сессия. Напиши /stop.")
        return

    topic = None
    if context.args and len(context.args) > 0 and context.args[0].startswith('#'):
        topic = context.args[0][1:].lower()

    cards = get_words_for_learn(10, topic)
    if not cards:
        if topic:
            await update.message.reply_text(f"Нет слов для обучения по теме #{topic}. Добавь слова с этим тегом или выбери другую тему.")
        else:
            await update.message.reply_text("Нет слов для обучения.\nСначала возьми новые слова через /daily_words\nИли добавь слова вручную через `Добавь слово X — Y`")
        return

    sessions[uid] = {
        "mode": "learn",
        "cards": cards,
        "index": 0,
        "direction": "ru_to_en",
        "learned_words": [],
        "skip_words": [],
        "topic": topic
    }

    await update.message.reply_text(
        f"🧠 *Начинаем обучение!* Будет {len(cards)} слов.\n"
        f"Сначала я покажу русское слово, ты переводишь на английский.\n"
        f"Потом наоборот — английское → русский.\n\n"
        f"Для каждого слова нажимай кнопку:\n"
        f"✅ *Выучено* — слово перейдёт в категорию Learned\n"
        f"🔄 *Ещё раз* — слово останется для повторения\n\n"
        f"Начинаем!",
        parse_mode="Markdown"
    )
    await show_next(update, uid)

async def show_next(update, uid):
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
            await update.message.reply_text("🔄 Теперь АНГЛИЙСКИЙ → РУССКИЙ")
            await show_next(update, uid)
            return
        else:
            learned = session.get("learned_words", [])
            if learned:
                for word_text in learned:
                    update_word_status_by_text(word_text, "learned")
                    w = get_word_entry_by_text(word_text)
                    if w:
                        w["interval"] = 1
                        w["next_review_date"] = (datetime.now() + timedelta(days=1)).isoformat()
                        save_words(words)
                await update.message.reply_text(
                    f"🎉 Ты выучил {len(learned)} слов! Они перешли в категорию 'Выучил'.\n"
                    f"Теперь ты можешь тренировать их через `/practice` или `/review`.\n\n"
                    f"Слова, которые ты отметил 'Ещё раз', остались для повторения."
                )
            else:
                await update.message.reply_text("Ты не выучил ни одного слова в этой сессии. Попробуй ещё раз!")
            del sessions[uid]
            return

    card = cards[idx]
    word = card["word"]
    word_en = word.split(" — ")[0]
    word_ru = word.split(" — ")[1]

    if direction == "ru_to_en":
        text = f"🔹 *{word_ru}*  ({idx+1}/{len(cards)})"
    else:
        text = f"🔹 *{word_en}*  ({idx+1}/{len(cards)})"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Выучено", callback_data=f"learn_yes_{uid}_{idx}"),
            InlineKeyboardButton("🔄 Ещё раз", callback_data=f"learn_no_{uid}_{idx}")
        ]
    ])

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split("_")
    if len(parts) < 3:
        return

    action = parts[1]
    try:
        uid = int(parts[2])
        idx = int(parts[3]) if len(parts) > 3 else 0
    except ValueError:
        return

    if uid not in sessions:
        await query.edit_message_text("Сессия уже завершена. Начни заново через /learn")
        return

    session = sessions[uid]
    if idx != session["index"]:
        await query.edit_message_text("Это слово уже обработано. Перехожу к следующему...")
        await show_next_from_callback(update, uid)
        return

    card = session["cards"][idx]
    word_text = card["word"].split(" — ")[0]

    if action == "yes":
        session.setdefault("learned_words", []).append(word_text)
        w = get_word_entry_by_text(word_text)
        if w and w.get("error_count", 0) > 0:
            w["error_count"] = max(0, w.get("error_count", 0) - 1)
            save_words(words)
        await query.edit_message_text(f"✅ '{word_text}' выучено! Перехожу к следующему...")
    else:
        session.setdefault("skip_words", []).append(word_text)
        w = get_word_entry_by_text(word_text)
        if w:
            w["error_count"] = w.get("error_count", 0) + 1
            save_words(words)
        await query.edit_message_text(f"🔄 '{word_text}' оставлено для повторения.")

    session["index"] += 1
    await show_next_from_callback(update, uid)

async def show_next_from_callback(update, uid):
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
            await update.effective_message.reply_text("🔄 Теперь АНГЛИЙСКИЙ → РУССКИЙ")
            await show_next_from_callback(update, uid)
            return
        else:
            learned = session.get("learned_words", [])
            if learned:
                for word_text in learned:
                    update_word_status_by_text(word_text, "learned")
                    w = get_word_entry_by_text(word_text)
                    if w:
                        w["interval"] = 1
                        w["next_review_date"] = (datetime.now() + timedelta(days=1)).isoformat()
                        save_words(words)
                await update.effective_message.reply_text(
                    f"🎉 Ты выучил {len(learned)} слов! Они перешли в категорию 'Выучил'.\n"
                    f"Слова, отмеченные 'Ещё раз', остались для повторения."
                )
            else:
                await update.effective_message.reply_text("Ты не выучил ни одного слова.")
            del sessions[uid]
            return

    card = cards[idx]
    word = card["word"]
    word_en = word.split(" — ")[0]
    word_ru = word.split(" — ")[1]

    if direction == "ru_to_en":
        text = f"🔹 *{word_ru}*  ({idx+1}/{len(cards)})"
    else:
        text = f"🔹 *{word_en}*  ({idx+1}/{len(cards)})"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Выучено", callback_data=f"learn_yes_{uid}_{idx}"),
            InlineKeyboardButton("🔄 Ещё раз", callback_data=f"learn_no_{uid}_{idx}")
        ]
    ])

    await update.effective_message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )

# === РЕЖИМ ПОВТОРЕНИЯ ===

async def review(update, context):
    uid = update.effective_user.id
    if uid in sessions:
        await update.message.reply_text("У тебя уже есть активная сессия. Напиши /stop.")
        return

    count = 15
    if context.args and len(context.args) > 0:
        try:
            count = int(context.args[0])
            if count < 1:
                count = 1
        except ValueError:
            await update.message.reply_text("Укажи число, например: /review 15")
            return

    cards = get_words_for_review(count)
    if not cards:
        await update.message.reply_text(
            "Нет слов для повторения по интервальному графику.\n"
            "Возможно, все слова ещё не подошли по времени.\n"
            "Используй /learn для изучения новых слов."
        )
        return

    sessions[uid] = {
        "mode": "review",
        "cards": cards,
        "index": 0,
        "direction": "ru_to_en"
    }

    await update.message.reply_text(
        f"🔄 *Начинаем повторение!* Будет {len(cards)} слов.\n"
        f"Переведи русское слово на английский.\n"
        f"Правильный ответ подтвердит, что ты помнишь слово.\n\n"
        f"✍️ Отвечай на каждое слово. /stop — выйти."
    )
    await show_next(update, uid)

# === РЕЖИМ ПРАКТИКИ ===

async def practice(update, context):
    count = 10
    difficulty = "medium"
    if context.args and len(context.args) > 0:
        try:
            if context.args[0] in ["easy", "medium", "hard"]:
                difficulty = context.args[0]
                if len(context.args) > 1:
                    count = int(context.args[1])
            else:
                count = int(context.args[0])
                if len(context.args) > 1 and context.args[1] in ["easy", "medium", "hard"]:
                    difficulty = context.args[1]
        except ValueError:
            if context.args[0] in ["easy", "medium", "hard"]:
                difficulty = context.args[0]
            else:
                await update.message.reply_text("Укажи сложность (easy/medium/hard) или число, например: /practice easy 10")
                return

    words_for_practice = get_words_for_practice(count, difficulty)
    if not words_for_practice:
        await update.message.reply_text(
            f"Нет выученных слов для практики (сложность {difficulty}).\n"
            "Сначала выучи слова через /learn или /daily_words"
        )
        return

    await update.message.reply_text("⏳ Генерирую предложения...")
    sentences = generate_practice_sentences(words_for_practice, count, difficulty)

    parts = split_text(sentences, 4000)
    header = f"📝 *ПРАКТИКА ПЕРЕВОДА ({count} предложений, сложность: {difficulty})*\n\n"
    
    for i, part in enumerate(parts):
        if i == 0:
            await update.message.reply_text(header + part, parse_mode="Markdown")
        else:
            await update.message.reply_text(part, parse_mode="Markdown")
    
    await update.message.reply_text("✍️ Переведи предложения на английский и отправь мне. Я проверю все переводы!")

# === РЕЖИМ ДИАЛОГА ===

async def dialogue(update, context):
    uid = update.effective_user.id
    available = get_words_by_status("learned") + get_words_by_status("review")
    if len(available) < 5:
        await update.message.reply_text("Недостаточно выученных слов для диалога. Выучи больше слов через /learn.")
        return
    selected = random.sample(available, min(10, len(available)))
    
    await update.message.reply_text("🗣️ Генерирую диалог на IT-тему...")
    dialogue_text = generate_dialogue(selected, topic="general")
    
    parts = split_text(dialogue_text, 4000)
    header = "🗣️ *Диалог для практики*\n\n"
    
    for i, part in enumerate(parts):
        if i == 0:
            await update.message.reply_text(header + part, parse_mode="Markdown")
        else:
            await update.message.reply_text(part, parse_mode="Markdown")
    
    await update.message.reply_text("📌 Прочитай диалог вслух, попробуй использовать эти слова в своей речи.")

# === ИЗМЕНЕНИЕ СТАТУСА ===

async def set_status(update, context):
    text = update.message.text
    result = detect_status_command(text)
    if not result:
        await update.message.reply_text(
            "Используй формат: /status слово — status\n"
            "Доступные статусы: new, learning, learned, review\n\n"
            "Пример: /status pivot — learned"
        )
        return

    word_text, new_status = result
    valid_statuses = ["new", "learning", "learned", "review"]
    if new_status not in valid_statuses:
        await update.message.reply_text(f"Неверный статус. Доступны: {', '.join(valid_statuses)}")
        return

    word_entry = get_word_entry_by_text(word_text)
    if not word_entry:
        await update.message.reply_text(f"Слово '{word_text}' не найдено в словаре.")
        return

    old_status = word_entry.get("status", "unknown")
    update_word_status_by_text(word_text, new_status)

    status_emojis = {
        "new": "🆕",
        "learning": "📖",
        "learned": "✅",
        "review": "🔄"
    }

    await update.message.reply_text(
        f"✅ Статус слова обновлён!\n\n"
        f"{word_entry['word']}\n"
        f"{status_emojis.get(old_status, '❓')} → {status_emojis.get(new_status, '❓')}"
    )

async def stop(update, context):
    uid = update.effective_user.id
    if uid in sessions:
        del sessions[uid]
        await update.message.reply_text("⏹️ Сессия остановлена.")
    else:
        await update.message.reply_text("ℹ️ Нет активной сессии.")

async def toggle(update, context):
    uid = str(update.effective_user.id)
    if uid not in user_data:
        user_data[uid] = {}
    cmd = update.message.text.lower()
    if "on" in cmd:
        user_data[uid]["receives_daily"] = True
        save_user_data(user_data)
        await update.message.reply_text("✅ Ежедневная рассылка включена.")
    else:
        user_data[uid]["receives_daily"] = False
        save_user_data(user_data)
        await update.message.reply_text("❌ Ежедневная рассылка выключена.")

# === ОСНОВНОЙ ОБРАБОТЧИК ===

async def handle(update, context):
    uid = update.effective_user.id
    text = update.message.text

    if uid in sessions and sessions[uid].get("mode") == "review":
        session = sessions[uid]
        idx = session["index"]
        if idx >= len(session["cards"]):
            await show_next(update, uid)
            return

        card = session["cards"][idx]
        word_text = card["word"].split(" — ")[0]
        correct_word = word_text if session["direction"] == "en_to_ru" else card["word"].split(" — ")[1]

        await update.message.chat.send_action(action="typing")
        feedback = check_translation(text, correct_word)
        is_correct = "✅" in feedback or "правильно" in feedback.lower()
        update_word_after_review(word_text, is_correct)
        await update.message.reply_text(feedback)

        session["index"] += 1
        await show_next(update, uid)
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
            + (f"Тема: #{topic}\n" if topic else "")
            + f"\n📚 Теперь в словаре {len(words)} слов.\n"
            f"💡 Введи /daily_words чтобы начать изучение."
        )
        return

    if len(text.split('\n')) >= 2:
        await update.message.chat.send_action(action="typing")
        await update.message.reply_text("🔍 Проверяю переводы...")
        result = check_sentences(text)
        await update.message.reply_text(result)
        return

    await update.message.chat.send_action(action="typing")
    resp = ask_deepseek(uid, text)
    await update.message.reply_text(resp)

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
    app.add_handler(CommandHandler("status", set_status))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("on", toggle))
    app.add_handler(CommandHandler("off", toggle))

    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    scheduler.add_job(send_daily_tasks, CronTrigger(hour=9, minute=0), id='daily')

    print("✅ Бот запущен!")
    print("📚 /daily_words [тема] — взять 5 новых слов с примерами")
    print("🧠 /learn [тема] — обучение с кнопками")
    print("🔄 /review — интервальное повторение")
    print("📝 /practice [easy|medium|hard] — практика предложений")
    print("🗣️ /dialogue — диалог на IT-тему")
    print("🔴 /weak — топ ошибок")
    print("📈 /progress — прогресс по дням")
    print("🎯 /status слово — status (new/learning/learned/review)")
    print("⏰ Ежедневная рассылка в 09:00")

    app.run_polling()

if __name__ == "__main__":
    main()
