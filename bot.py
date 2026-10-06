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

# === НАСТРОЙКИ ===
load_dotenv()
logging.basicConfig(level=logging.INFO)

deepseek_client = OpenAI(
    api_key=os.environ.get('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com"
)

# === ФАЙЛЫ ===
WORDS_FILE = "words.json"
PHRASAL_FILE = "phrasal.json"
GRAMMAR_FILE = "grammar.json"
USER_DATA_FILE = "user_data.json"

# === ЗАГРУЗКА/СОХРАНЕНИЕ ===
def load_json(path, default):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# === ДЕФОЛТНЫЙ СПИСОК ФРАЗОВЫХ ГЛАГОЛОВ (200+) ===
DEFAULT_PHRASAL_VERBS = [
    "ask out — пригласить на свидание",
    "back up — поддержать; сделать резервную копию",
    "blow up — взорвать; разозлиться",
    "break down — сломаться; потерять контроль",
    "break into — проникнуть; ворваться",
    "break out — вырваться; вспыхнуть (о войне)",
    "break up — расстаться; разбить на части",
    "bring about — вызвать, привести к",
    "bring down — свергнуть; снизить",
    "bring up — воспитывать; поднять тему",
    "call back — перезвонить",
    "call off — отменить",
    "call on — навестить; призвать",
    "call up — позвонить; призвать в армию",
    "carry on — продолжать",
    "carry out — выполнить, осуществить",
    "catch up — догнать; наверстать",
    "check in — зарегистрироваться",
    "check out — выписаться; проверить",
    "cheer up — подбодрить",
    "clear up — прояснить; убрать",
    "come across — наткнуться, случайно найти",
    "come along — идти вместе; появляться",
    "come around — зайти; передумать",
    "come back — вернуться",
    "come down with — заболеть чем-то",
    "come forward — выйти вперёд, откликнуться",
    "come off — отвалиться; удаться",
    "come out — выйти; стать известным",
    "come over — зайти в гости",
    "come through — пережить; справиться",
    "come up — возникнуть; приблизиться",
    "come up with — придумать",
    "count on — рассчитывать на",
    "cut down — сократить; срубить",
    "cut off — отрезать; отключить",
    "deal with — иметь дело с; справляться",
    "do away with — покончить с, отменить",
    "do over — переделать",
    "do up — застегнуть; отремонтировать",
    "do without — обходиться без",
    "draw up — составить (документ)",
    "drop by — зайти на минутку",
    "drop off — завезти; заснуть",
    "drop out — бросить (учёбу)",
    "end up — оказаться в итоге",
    "fall apart — развалиться",
    "fall behind — отставать",
    "fall for — влюбиться; попасться",
    "fall out — поссориться",
    "figure out — разобраться, понять",
    "fill in — заполнить; заменить",
    "fill out — заполнить (форму)",
    "find out — выяснить",
    "follow up — следить, продолжать",
    "get along — ладить",
    "get around — обходить; передвигаться",
    "get away — уйти, сбежать",
    "get back — вернуться; вернуть",
    "get by — выживать, справляться",
    "get down — расстраивать; спускаться",
    "get in — попасть внутрь; прибыть",
    "get into — войти; увлечься",
    "get off — выйти (из транспорта); отделаться",
    "get on — сесть (в транспорт); ладить",
    "get out — выйти; выбраться",
    "get over — преодолеть; оправиться",
    "get through — пройти через; дозвониться",
    "get together — собраться",
    "get up — встать",
    "give away — отдать; выдать (секрет)",
    "give back — вернуть",
    "give in — уступить",
    "give out — раздать; закончиться",
    "give up — сдаться, бросить",
    "go after — преследовать",
    "go ahead — продолжать; давать зелёный свет",
    "go away — уйти",
    "go back — вернуться",
    "go down — падать, снижаться; происходить",
    "go for — выбирать; атаковать",
    "go in — войти",
    "go off — взорваться; сработать (будильник)",
    "go on — продолжать; происходить",
    "go out — выходить; гаснуть",
    "go over — просматривать",
    "go through — пройти через, пережить",
    "go up — подниматься, расти",
    "grow up — вырастать",
    "hand in — сдать (работу)",
    "hand out — раздавать",
    "hang on — подождать; держаться",
    "hang out — тусоваться",
    "hang up — повесить трубку",
    "hold back — сдерживать",
    "hold on — держаться; подождать",
    "hold up — задерживать; ограбить",
    "keep on — продолжать",
    "keep up — не отставать; поддерживать",
    "knock down — сбить; снести",
    "knock out — вырубить; поразить",
    "lay off — уволить",
    "leave out — пропустить, не включать",
    "let down — подвести",
    "let in — впустить",
    "let out — выпустить; издать (звук)",
    "look after — заботиться",
    "look at — смотреть на",
    "look down on — смотреть свысока",
    "look for — искать",
    "look forward to — с нетерпением ждать",
    "look into — изучать, разбираться",
    "look out — осторожно!",
    "look over — просматривать",
    "look up — искать (в словаре); навестить",
    "look up to — уважать",
    "make out — разобрать; целоваться",
    "make up — придумать; помириться; накраситься",
    "make up for — компенсировать",
    "move in — въехать",
    "move on — двигаться дальше",
    "move out — выехать",
    "pass away — умереть",
    "pass by — пройти мимо",
    "pass out — потерять сознание",
    "pay back — вернуть долг",
    "pay off — окупиться; расплатиться",
    "pick out — выбрать",
    "pick up — подобрать; забрать",
    "point out — указать на",
    "pull off — осуществить (сложное)",
    "pull out — вытащить; выйти (из сделки)",
    "pull over — прижаться к обочине",
    "pull through — выжить, справиться",
    "pull up — подъехать; подтянуться",
    "put away — убрать",
    "put down — положить; унизить; усыпить",
    "put forward — предложить",
    "put off — отложить; оттолкнуть",
    "put on — надеть; набрать (вес)",
    "put out — потушить; вывести из себя",
    "put through — соединить по телефону",
    "put up — поднять; построить; приютить",
    "put up with — мириться с",
    "run into — случайно встретить; врезаться",
    "run out — закончиться",
    "run over — переехать; просмотреть",
    "set off — отправиться; взорвать",
    "set out — отправиться; изложить",
    "set up — установить; основать",
    "show off — хвастаться",
    "show up — появиться",
    "shut down — закрыть; выключить",
    "shut up — замолчать",
    "sit down — сесть",
    "sort out — разобраться; уладить",
    "stand by — поддерживать; быть готовым",
    "stand for — обозначать; выступать за",
    "stand out — выделяться",
    "stand up — встать; подвести (о шутке)",
    "stand up for — защищать",
    "stay up — не спать",
    "stick to — придерживаться",
    "take after — быть похожим",
    "take apart — разобрать на части",
    "take away — унести; убрать",
    "take back — взять назад",
    "take down — снять; записать",
    "take in — понять; обмануть; ушить",
    "take off — взлететь; снять (одежду)",
    "take on — взять (работу); нанять",
    "take out — вынуть; пригласить",
    "take over — взять под контроль",
    "take up — заняться; занять (место)",
    "talk into — уговорить",
    "talk out of — отговорить",
    "tell off — отругать",
    "think over — обдумать",
    "throw away — выбросить",
    "try on — примерить",
    "try out — испытать",
    "turn around — развернуться; улучшиться",
    "turn down — отвергнуть; убавить",
    "turn in — сдать; лечь спать",
    "turn off — выключить; оттолкнуть",
    "turn on — включить; напасть",
    "turn out — оказаться",
    "turn over — перевернуть",
    "turn up — появиться; сделать громче",
    "wake up — проснуться",
    "warm up — разогреться",
    "wear out — износить; утомить",
    "work out — тренироваться; получиться; решить",
    "write down — записать",
    "write off — списать; считать неудачей",
    "zip up — застегнуть молнию"
]

DEFAULT_GRAMMAR_TOPICS = [
    {"id": 1, "title": "Present Perfect vs Past Simple", "level": "B2", "status": "new"},
    {"id": 2, "title": "Past Perfect Continuous", "level": "B2", "status": "new"},
    {"id": 3, "title": "Future Perfect & Future Continuous", "level": "B2", "status": "new"},
    {"id": 4, "title": "Conditionals (Type 0, 1, 2, 3)", "level": "B2", "status": "new"},
    {"id": 5, "title": "Mixed Conditionals", "level": "B2", "status": "new"},
    {"id": 6, "title": "Passive Voice (all tenses)", "level": "B2", "status": "new"},
    {"id": 7, "title": "Reported Speech", "level": "B2", "status": "new"},
    {"id": 8, "title": "Modal Verbs of Deduction", "level": "B2", "status": "new"},
    {"id": 9, "title": "Gerund vs Infinitive", "level": "B2", "status": "new"},
    {"id": 10, "title": "Relative Clauses (defining & non-defining)", "level": "B2", "status": "new"},
    {"id": 11, "title": "Inversion", "level": "C1", "status": "new"},
    {"id": 12, "title": "Cleft Sentences", "level": "C1", "status": "new"},
    {"id": 13, "title": "Participle Clauses", "level": "C1", "status": "new"},
    {"id": 14, "title": "Subjunctive & Unreal Past", "level": "C1", "status": "new"},
    {"id": 15, "title": "Ellipsis & Substitution", "level": "C1", "status": "new"}
]

# === ЗАГРУЖАЕМ ДАННЫЕ ===
words = load_json(WORDS_FILE, [])
phrasal_verbs = load_json(PHRASAL_FILE, [])
grammar_topics = load_json(GRAMMAR_FILE, [])

# Инициализация файлов при первом запуске
if not words and os.path.exists(WORDS_FILE) == False:
    save_json(WORDS_FILE, [])

if not phrasal_verbs:
    # Создаём фразовые глаголы из дефолтного списка
    phrasal_verbs = [
        {
            "word": pv,
            "status": "new",
            "added_at": datetime.now().isoformat(),
            "learned_at": None,
            "error_count": 0
        }
        for pv in DEFAULT_PHRASAL_VERBS
    ]
    save_json(PHRASAL_FILE, phrasal_verbs)

if not grammar_topics:
    grammar_topics = DEFAULT_GRAMMAR_TOPICS
    save_json(GRAMMAR_FILE, grammar_topics)

user_data = load_json(USER_DATA_FILE, {})
user_histories = {}

sessions = {}
scheduler = AsyncIOScheduler()

# === КОНСТАНТЫ ===
REVIEW_INTERVALS = {"review_1": 3, "review_2": 7, "review_3": 14}
NEXT_STATUS = {"learning": "review_1", "review_1": "review_2", "review_2": "review_3", "review_3": "mastered"}

# === УТИЛИТЫ ===
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

# === РАБОТА С ТРЕКАМИ ===
def get_track(track):
    """Возвращает список слов трека: 'main' или 'phrasal'"""
    if track == "main":
        return words
    elif track == "phrasal":
        return phrasal_verbs
    return []

def save_track(track):
    if track == "main":
        save_json(WORDS_FILE, words)
    elif track == "phrasal":
        save_json(PHRASAL_FILE, phrasal_verbs)

def words_by_status(track, status):
    return [w for w in get_track(track) if w.get("status") == status]

def words_by_statuses(track, statuses):
    return [w for w in get_track(track) if w.get("status") in statuses]

def get_word_entry(track, word_text):
    for w in get_track(track):
        if w["word"].split(" — ")[0].lower() == word_text.lower():
            return w
    return None

def update_status(track, word_text, new_status):
    for w in get_track(track):
        if w["word"].split(" — ")[0].lower() == word_text.lower():
            w["status"] = new_status
            if new_status == "learning":
                w["learned_at"] = datetime.now().isoformat()
            elif new_status in REVIEW_INTERVALS:
                w["next_review_date"] = (datetime.now() + timedelta(days=REVIEW_INTERVALS[new_status])).isoformat()
            elif new_status == "mastered":
                w["next_review_date"] = None
            save_track(track)
            return True
    return False

def get_learn_cards(track, limit=10):
    learning = words_by_status(track, "learning")
    learning.sort(key=lambda x: x.get("learned_at", "1970-01-01"))
    now = datetime.now()
    review_statuses = ["review_1", "review_2", "review_3"]
    review = words_by_statuses(track, review_statuses)
    due = [w for w in review if w.get("next_review_date") and datetime.fromisoformat(w["next_review_date"]) <= now]
    due.sort(key=lambda x: x.get("next_review_date", "2099-01-01"))
    all_cards = learning + due
    return all_cards[:limit]

def get_practice_words(track, limit=10):
    mastered = words_by_status(track, "mastered")
    review = words_by_statuses(track, ["review_1", "review_2", "review_3"])
    available = mastered + review
    if not available:
        return []
    return random.sample(available, min(limit, len(available)))

# === DEEPSEEK ===
def generate_examples(word_entries):
    if not word_entries:
        return None
    prompt = f"""Для каждого из следующих слов напиши ровно 2 примера предложений на английском с переводом на русский.
IT-сфера. Слова: {', '.join([w['word'].split(' — ')[0] for w in word_entries])}
Формат:
Слово: [слово]
1. [англ] — [рус]
2. [англ] — [рус]
"""
    try:
        r = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return r.choices[0].message.content
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        return None

def generate_sentences_russian(word_entries, count=5):
    """Предложения на русском для перевода на английский"""
    if not word_entries:
        return None
    word_list = ", ".join([w['word'].split(' — ')[0] for w in word_entries])
    prompt = f"""Составь {count} предложений на РУССКОМ языке для перевода на английский.
В каждом предложении используй одно из слов: {word_list}
IT-сфера (Product Management, аналитика, разработка).
Формат: пронумерованный список 1..{count}."""
    try:
        r = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return r.choices[0].message.content
    except:
        return None

def generate_sentences_english(word_entries, count=5):
    """Предложения на английском для перевода на русский"""
    if not word_entries:
        return None
    word_list = ", ".join([w['word'].split(' — ')[0] for w in word_entries])
    prompt = f"""Составь {count} предложений на АНГЛИЙСКОМ языке для перевода на русский.
В каждом предложении используй одно из слов: {word_list}
IT-сфера (Product Management, аналитика, разработка).
Формат: пронумерованный список 1..{count}."""
    try:
        r = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return r.choices[0].message.content
    except:
        return None

def check_translation(user_text, correct_text):
    prompt = f"""Пользователь перевёл.
ПРАВИЛЬНЫЙ ОТВЕТ: {correct_text}
ОТВЕТ ПОЛЬЗОВАТЕЛЯ: {user_text}
Оцени: правильно или нет. Если ошибка — объясни кратко (макс 3 предложения)."""
    try:
        r = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return r.choices[0].message.content
    except:
        return f"✅ Правильный ответ: {correct_text}"

def check_sentence_translation(user_text, sentence, direction):
    """Проверка перевода целого предложения"""
    if direction == "ru_to_en":
        prompt = f"""Пользователь перевёл русское предложение на английский.
РУССКОЕ ПРЕДЛОЖЕНИЕ: {sentence}
ПЕРЕВОД ПОЛЬЗОВАТЕЛЯ: {user_text}
Оцени: правильно или нет. Укажи ошибки, предложи правильный вариант. Макс 5 строк."""
    else:
        prompt = f"""Пользователь перевёл английское предложение на русский.
АНГЛИЙСКОЕ ПРЕДЛОЖЕНИЕ: {sentence}
ПЕРЕВОД ПОЛЬЗОВАТЕЛЯ: {user_text}
Оцени: правильно или нет. Укажи ошибки, предложи правильный вариант. Макс 5 строк."""
    try:
        r = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return r.choices[0].message.content
    except:
        return "⚠️ Не удалось проверить."

def generate_grammar_lesson(topic_title, level):
    prompt = f"""Тема: "{topic_title}" (уровень {level}).
Составь мини-урок:
1. Краткое объяснение правила (5-7 строк) с примерами.
2. 5 упражнений: предложения с пропусками, где нужно вставить правильную форму/структуру.
Формат:
📖 Правило:
[объяснение]

✍️ Упражнения:
1. [предложение с ______]
2. ...
"""
    try:
        r = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return r.choices[0].message.content
    except:
        return None

def check_grammar_answers(user_text, lesson):
    prompt = f"""Урок:
{lesson}

Ответы пользователя:
{user_text}

Проверь ответы, укажи ошибки и правильные варианты. Макс 10 строк."""
    try:
        r = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return r.choices[0].message.content
    except:
        return "⚠️ Не удалось проверить."

def ask_deepseek(user_id, text):
    if user_id not in user_histories:
        user_histories[user_id] = [{"role": "system", "content": "Ты репетитор английского для IT."}]
    user_histories[user_id].append({"role": "user", "content": text})
    try:
        r = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=user_histories[user_id],
            stream=False
        )
        ans = r.choices[0].message.content
        user_histories[user_id].append({"role": "assistant", "content": ans})
        if len(user_histories[user_id]) > 20:
            user_histories[user_id] = [user_histories[user_id][0]] + user_histories[user_id][-19:]
        return ans
    except:
        return "Ошибка."

# === ДЕТЕКТОРЫ ===
def detect_add_word(text):
    m = re.search(r'добавь\s+слово\s+(.+?)\s*[-—]\s*(.+)', text, re.IGNORECASE)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    m = re.search(r'^\+ (.+?)\s*[-—]\s*(.+)$', text.strip())
    if m:
        return (m.group(1).strip(), m.group(2).strip(), "learning")
    return None

def detect_status_command(text):
    m = re.search(r'/status\s+(.+?)\s+[-—]\s+(.+)', text, re.IGNORECASE)
    if m:
        return (m.group(1).strip(), m.group(2).strip().lower())
    return None

def detect_phrasal_add(text):
    m = re.search(r'^фг\s+(.+?)\s*[-—]\s*(.+)$', text.strip(), re.IGNORECASE)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    return None

# === КЛАВИАТУРЫ ===
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Взять новые слова", callback_data="menu_daily")],
        [InlineKeyboardButton("🧠 Тренировка слов", callback_data="menu_learn")],
        [InlineKeyboardButton("📝 Тренировка предложений", callback_data="menu_translation")],
        [InlineKeyboardButton("📋 Слова на обучении", callback_data="menu_learning")],
        [InlineKeyboardButton("📊 Статистика", callback_data="menu_stats")],
        [InlineKeyboardButton("🔤 Фразовые глаголы", callback_data="menu_phrasal")],
        [InlineKeyboardButton("📚 Грамматика B2-C1", callback_data="menu_grammar")],
        [InlineKeyboardButton("🔧 Дополнительно", callback_data="menu_more")]
    ])

def more_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Практика Cloze", callback_data="menu_practice")],
        [InlineKeyboardButton("🗣️ Диалог", callback_data="menu_dialogue")],
        [InlineKeyboardButton("🔴 Слабые слова", callback_data="menu_weak")],
        [InlineKeyboardButton("📈 Прогресс", callback_data="menu_progress")],
        [InlineKeyboardButton("🔄 Сброс learning", callback_data="menu_reset_learning")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_back")]
    ])

def phrasal_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Взять 5 новых ФГ", callback_data="phrasal_daily")],
        [InlineKeyboardButton("🧠 Учить ФГ", callback_data="phrasal_learn")],
        [InlineKeyboardButton("📊 Статистика ФГ", callback_data="phrasal_stats")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_back")]
    ])

def grammar_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Новая тема", callback_data="grammar_new")],
        [InlineKeyboardButton("📊 Прогресс грамматики", callback_data="grammar_progress")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_back")]
    ])

def learn_kb(uid, idx):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💡 Подсказка", callback_data=f"hint_{uid}_{idx}")],
        [
            InlineKeyboardButton("❌ Пропустить", callback_data=f"skip_{uid}_{idx}"),
            InlineKeyboardButton("⏹️ Закончить", callback_data=f"stop_{uid}")
        ]
    ])

def translation_kb(uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ Пропустить", callback_data=f"trans_skip_{uid}")],
        [InlineKeyboardButton("⏹️ Закончить тренировку", callback_data=f"trans_stop_{uid}")]
    ])

# === ЕЖЕДНЕВНАЯ РАССЫЛКА ===
async def send_daily_tasks():
    bot = Bot(token=os.environ.get('TELEGRAM_BOT_TOKEN'))
    users = load_json(USER_DATA_FILE, {})
    for uid_str, data in users.items():
        if data.get("receives_daily", True):
            try:
                new_count = len(words_by_status("main", "new"))
                learning_count = len(words_by_status("main", "learning"))
                reminder = ""
                if new_count > 0:
                    reminder += f"\n📌 Новых слов: {new_count}. Введи /daily_words"
                if learning_count > 0:
                    reminder += f"\n📖 {learning_count} слов ждут тренировки! Введи /learn"
                reminder += "\n\n🔤 Не забудь про фразовые глаголы: /daily_phrasal"
                reminder += "\n📚 И грамматика: /grammar"
                await bot.send_message(chat_id=int(uid_str), text="🌞 Доброе утро!" + reminder)
            except Exception as e:
                logging.error(f"Ошибка: {e}")

# === КОМАНДЫ ===
async def start(update, context):
    uid = str(update.effective_user.id)
    if uid not in user_data:
        user_data[uid] = {"receives_daily": True, "grammar_progress": 0}
        save_json(USER_DATA_FILE, user_data)
    text = ("👋 *Привет! Я — система изучения английского для IT!*\n\n"
            f"📥 Новых (main): {len(words_by_status('main', 'new'))}\n"
            f"📖 Учу (main): {len(words_by_status('main', 'learning'))}\n"
            f"🔤 Фразовых глаголов новых: {len(words_by_status('phrasal', 'new'))}\n"
            f"📚 Грамматика: тема {user_data[uid].get('grammar_progress', 0) + 1}\n\n"
            "Выбери действие:")
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_kb())
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())

# === БОЛЬШОЙ ОБРАБОТЧИК КНОПОК ===
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid = update.effective_user.id

    if data == "menu_back":
        await start(update, context)
        return
    if data == "menu_more":
        await q.edit_message_text("🔧 *Дополнительные функции:*", parse_mode="Markdown", reply_markup=more_kb())
        return
    if data == "menu_daily":
        await daily_words(update, context, track="main")
    elif data == "menu_learn":
        await learn_start(update, context, track="main")
    elif data == "menu_translation":
        await translation_start(update, context)
    elif data == "menu_learning":
        await show_learning(update, context, track="main")
    elif data == "menu_stats":
        await stats(update, context)
    elif data == "menu_phrasal":
        await q.edit_message_text("🔤 *Фразовые глаголы:*", parse_mode="Markdown", reply_markup=phrasal_kb())
    elif data == "menu_grammar":
        await q.edit_message_text("📚 *Грамматика B2-C1:*", parse_mode="Markdown", reply_markup=grammar_kb())
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

    # Фразовые глаголы
    elif data == "phrasal_daily":
        await daily_words(update, context, track="phrasal")
    elif data == "phrasal_learn":
        await learn_start(update, context, track="phrasal")
    elif data == "phrasal_stats":
        await stats(update, context, track="phrasal")

    # Грамматика
    elif data == "grammar_new":
        await grammar_new(update, context)
    elif data == "grammar_progress":
        await grammar_progress(update, context)

    # Обучение (кнопки hint/skip/stop)
    elif data.startswith("hint_"):
        await handle_hint(update, context)
    elif data.startswith("skip_"):
        await handle_skip(update, context)
    elif data.startswith("stop_"):
        await finish_learn(update, context)
    elif data.startswith("trans_skip_"):
        await translation_skip(update, context)
    elif data.startswith("trans_stop_"):
        await translation_finish(update, context)

# === ОБУЧЕНИЕ СЛОВ (универсально для main и phrasal) ===
async def learn_start(update, context, track="main"):
    uid = update.effective_user.id
    if uid in sessions:
        # Разрешаем перезапуск (удаляем старую)
        del sessions[uid]
    cards = get_learn_cards(track, 10)
    if not cards:
        msg = "📚 Нет слов для тренировки." + (" Сначала возьми /daily_words." if track == "main" else " Сначала возьми /daily_phrasal.")
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(msg, reply_markup=main_kb())
        return
    sessions[uid] = {
        "mode": "learn",
        "track": track,
        "cards": cards,
        "index": 0,
        "direction": "ru_to_en",
        "results": [],
        "errors": [],
        "waiting_for_answer": True,
        "current_correct": "",
        "current_message_id": None
    }
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text(f"🧠 *Тренировка ({'ФГ' if track == 'phrasal' else 'слова'})!* {len(cards)} карточек.\n✍️ Пиши перевод вручную.", parse_mode="Markdown")
    await show_learn_card(update, uid)

async def show_learn_card(update, uid):
    if uid not in sessions:
        return
    s = sessions[uid]
    idx = s["index"]
    cards = s["cards"]
    direction = s["direction"]
    if idx >= len(cards):
        if direction == "ru_to_en":
            s["direction"] = "en_to_ru"
            s["index"] = 0
            target = update.callback_query.message if update.callback_query else update.message
            await target.reply_text("🔄 Теперь EN → RU")
            await show_learn_card(update, uid)
            return
        else:
            await finish_learn(update, uid)
            return
    card = cards[idx]
    word = card["word"]
    en = word.split(" — ")[0]
    ru = word.split(" — ")[1]
    if direction == "ru_to_en":
        text = f"📖 *{ru}*  ({idx+1}/{len(cards)})\n\n✍️ Напиши перевод на английский:"
        s["current_correct"] = en
    else:
        text = f"📖 *{en}*  ({idx+1}/{len(cards)})\n\n✍️ Напиши перевод на русский:"
        s["current_correct"] = ru
    s["waiting_for_answer"] = True
    kb = learn_kb(uid, idx)
    target = update.callback_query.message if update.callback_query else update.message
    msg = await target.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    s["current_message_id"] = msg.message_id

async def handle_hint(update, context):
    q = update.callback_query
    uid = update.effective_user.id
    if uid not in sessions or sessions[uid]["mode"] != "learn":
        await q.answer("Нет активной сессии")
        return
    s = sessions[uid]
    idx = s["index"]
    if idx >= len(s["cards"]):
        return
    card = s["cards"][idx]
    hint_word = card["word"].split(" — ")[1 if s["direction"] == "ru_to_en" else 0]
    await q.message.reply_text(f"💡 Подсказка: начинается с '{hint_word[0].upper()}'...")
    await q.answer("Подсказка отправлена")

async def handle_skip(update, context):
    q = update.callback_query
    uid = update.effective_user.id
    if uid not in sessions or sessions[uid]["mode"] != "learn":
        await q.answer("Нет активной сессии")
        return
    s = sessions[uid]
    idx = s["index"]
    if idx >= len(s["cards"]):
        return
    word_text = s["cards"][idx]["word"].split(" — ")[0]
    s["results"].append({"word": word_text, "correct": False})
    s["errors"].append(word_text)
    s["index"] += 1
    s["waiting_for_answer"] = False
    await q.answer("Пропущено")
    try:
        await q.message.delete()
    except:
        pass
    await q.message.reply_text("⏭️ Пропущено.")
    await show_learn_card(update, uid)

async def finish_learn(update, uid):
    if uid not in sessions:
        return
    s = sessions[uid]
    track = s.get("track", "main")
    results = s.get("results", [])
    errors = list(set(s.get("errors", [])))
    correct = len([r for r in results if r["correct"]])
    text = f"📊 *Результаты*\n✅ {correct}\n❌ {len(results) - correct}\n"
    for r in results:
        if r["correct"]:
            update_status(track, r["word"], NEXT_STATUS.get(get_word_entry(track, r["word"]).get("status"), "mastered"))
    if errors:
        text += "\n🔄 Ошибки:\n" + "\n".join(f"• {e}" for e in errors[:10])
    del sessions[uid]
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())

async def handle_learn_answer(update, context):
    uid = update.effective_user.id
    text = update.message.text
    s = sessions[uid]
    idx = s["index"]
    if idx >= len(s["cards"]):
        return
    card = s["cards"][idx]
    word_text = card["word"].split(" — ")[0]
    correct = s["current_correct"]
    feedback = check_translation(text, correct)
    is_correct = "✅" in feedback or "правильно" in feedback.lower()
    s["results"].append({"word": word_text, "correct": is_correct})
    if not is_correct:
        s["errors"].append(word_text)
        w = get_word_entry(s.get("track", "main"), word_text)
        if w:
            w["error_count"] = w.get("error_count", 0) + 1
            save_track(s.get("track", "main"))
    await update.message.reply_text(feedback)
    s["index"] += 1
    s["waiting_for_answer"] = False
    await show_learn_card(update, uid)

# === ТРЕНИРОВКА ПРЕДЛОЖЕНИЙ (translation) ===
async def translation_start(update, context):
    uid = update.effective_user.id
    if uid in sessions:
        del sessions[uid]

    # Берём слова в статусе learning
    learning = words_by_status("main", "learning")
    if len(learning) < 3:
        msg = "📝 Недостаточно слов в статусе learning (нужно минимум 3). Возьми новые через /daily_words."
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(msg, reply_markup=main_kb())
        return

    sample = random.sample(learning, min(5, len(learning)))
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text("⏳ Генерирую предложения...")

    ru_sentences = generate_sentences_russian(sample, count=5)
    en_sentences = generate_sentences_english(sample, count=5)

    # Парсим
    def parse_sentences(text):
        if not text:
            return []
        lines = []
        for line in text.split('\n'):
            line = line.strip()
            m = re.match(r'^\d+[\.\)]\s*(.+)', line)
            if m:
                lines.append(m.group(1).strip())
        return lines

    ru_list = parse_sentences(ru_sentences)
    en_list = parse_sentences(en_sentences)

    if not ru_list and not en_list:
        await target.reply_text("⚠️ Не удалось сгенерировать. Попробуй позже.")
        return

    sessions[uid] = {
        "mode": "translation",
        "direction": "ru_to_en",
        "ru_sentences": ru_list,
        "en_sentences": en_list,
        "sentences": ru_list,
        "index": 0,
        "current_sentence": "",
        "results": [],
        "words": sample
    }

    await target.reply_text(
        f"📝 *Тренировка предложений*\nБудет {len(ru_list)} предложений RU→EN, потом {len(en_list)} EN→RU.\n"
        f"Чтобы остановить — /stop",
        parse_mode="Markdown"
    )
    await show_translation_card(update, uid)

async def show_translation_card(update, uid):
    if uid not in sessions or sessions[uid]["mode"] != "translation":
        return
    s = sessions[uid]
    if s["index"] >= len(s["sentences"]):
        if s["direction"] == "ru_to_en" and s["en_sentences"]:
            s["direction"] = "en_to_ru"
            s["sentences"] = s["en_sentences"]
            s["index"] = 0
            target = update.message if update.message else update.callback_query.message
            await target.reply_text("🔄 Теперь EN → RU")
            await show_translation_card(update, uid)
            return
        else:
            await translation_finish(update, uid)
            return
    sentence = s["sentences"][s["index"]]
    s["current_sentence"] = sentence
    direction_label = "RU → EN" if s["direction"] == "ru_to_en" else "EN → RU"
    target = update.message if update.message else update.callback_query.message
    kb = translation_kb(uid)
    await target.reply_text(
        f"📝 *{direction_label}* ({s['index']+1}/{len(s['sentences'])})\n\n{sentence}\n\n✍️ Напиши перевод:",
        parse_mode="Markdown", reply_markup=kb
    )

async def handle_translation_answer(update, context):
    uid = update.effective_user.id
    text = update.message.text
    s = sessions[uid]
    sentence = s["current_sentence"]
    direction = s["direction"]
    await update.message.chat.send_action(action="typing")
    feedback = check_sentence_translation(text, sentence, direction)
    await update.message.reply_text(feedback)
    s["index"] += 1
    await show_translation_card(update, uid)

async def translation_skip(update, context):
    q = update.callback_query
    uid = update.effective_user.id
    if uid not in sessions or sessions[uid]["mode"] != "translation":
        await q.answer("Нет активной сессии")
        return
    sessions[uid]["index"] += 1
    await q.answer("Пропущено")
    await show_translation_card(update, uid)

async def translation_finish(update, uid):
    if uid not in sessions:
        return
    s = sessions[uid]
    target = update.message if update.message else update.callback_query.message
    await target.reply_text("✅ Тренировка предложений завершена!", reply_markup=main_kb())
    del sessions[uid]

# === ВЗЯТЬ НОВЫЕ СЛОВА ===
async def daily_words(update, context, track="main"):
    uid = str(update.effective_user.id)
    today = datetime.now().date()
    learning_today = [w for w in words_by_status(track, "learning")
                      if w.get("learned_at") and datetime.fromisoformat(w["learned_at"]).date() == today]
    if len(learning_today) >= 10:
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text("📚 Сегодня уже взято 10 слов.")
        return
    new = words_by_status(track, "new")
    if not new:
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text("🎉 Новых слов нет.")
        return
    available = min(5, 10 - len(learning_today), len(new))
    selected = random.sample(new, available)
    for w in selected:
        w["status"] = "learning"
        w["learned_at"] = datetime.now().isoformat()
    save_track(track)
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text("🧠 Генерирую примеры...")
    examples = generate_examples(selected)
    text = f"📚 *Взято {len(selected)} новых:*\n\n"
    for i, w in enumerate(selected, 1):
        text += f"{i}. {w['word']}\n"
    if examples:
        text += f"\n📝 *Примеры:*\n\n{examples}"
    text += "\n\n✍️ Дальше: /learn" + (" (или /learn_phrasal)" if track == "phrasal" else "")
    for part in split_text(text, 4000):
        await target.reply_text(part, parse_mode="Markdown")
    await target.reply_text("Выбери действие:", reply_markup=main_kb())

async def phrasal_daily(update, context):
    await daily_words(update, context, track="phrasal")

# === СТАТИСТИКА ===
async def stats(update, context, track="main"):
    new_c = len(words_by_status(track, "new"))
    l_c = len(words_by_status(track, "learning"))
    r1 = len(words_by_status(track, "review_1"))
    r2 = len(words_by_status(track, "review_2"))
    r3 = len(words_by_status(track, "review_3"))
    m = len(words_by_status(track, "mastered"))
    text = (f"📊 *Статистика ({'ФГ' if track == 'phrasal' else 'main'})*\n\n"
            f"📥 Новых: {new_c}\n📖 Учу: {l_c}\n"
            f"🔄 r1: {r1}, r2: {r2}, r3: {r3}\n"
            f"✅ Выучено: {m}\n📚 Всего: {len(get_track(track))}")
    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_kb())
    else:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())

async def show_learning(update, context, track="main"):
    lw = words_by_status(track, "learning")
    if not lw:
        text = "📋 Нет слов в обучении."
    else:
        text = "📋 *Слова на обучении:*\n\n" + "\n".join(f"{i}. {w['word']}" for i, w in enumerate(lw, 1))
        text += f"\n\n📊 Всего: {len(lw)}"
    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_kb())
    else:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())

# === ГРАММАТИКА ===
async def grammar_new(update, context):
    uid = str(update.effective_user.id)
    if uid not in user_data:
        user_data[uid] = {"receives_daily": True, "grammar_progress": 0}
        save_json(USER_DATA_FILE, user_data)
    idx = user_data[uid].get("grammar_progress", 0)
    if idx >= len(grammar_topics):
        await update.callback_query.message.reply_text("🎉 Все темы пройдены!")
        return
    topic = grammar_topics[idx]
    await update.callback_query.message.reply_text(f"⏳ Готовлю урок: *{topic['title']}*...", parse_mode="Markdown")
    lesson = generate_grammar_lesson(topic["title"], topic["level"])
    if not lesson:
        await update.callback_query.message.reply_text("⚠️ Не удалось сгенерировать. Попробуй позже.")
        return
    sessions[uid] = {
        "mode": "grammar",
        "topic_idx": idx,
        "lesson": lesson,
        "waiting_for_answer": True
    }
    for part in split_text(f"📚 *Урок {idx+1}/{len(grammar_topics)}: {topic['title']}*\n\n{lesson}", 4000):
        await update.callback_query.message.reply_text(part, parse_mode="Markdown")
    await update.callback_query.message.reply_text("✍️ Напиши ответы на упражнения. Я проверю. /stop — выйти.")

async def grammar_progress(update, context):
    uid = str(update.effective_user.id)
    idx = user_data.get(uid, {}).get("grammar_progress", 0)
    text = f"📚 Пройдено тем: {idx} из {len(grammar_topics)}\n\n"
    for i, t in enumerate(grammar_topics, 1):
        mark = "✅" if i <= idx else "⏳"
        text += f"{mark} {i}. {t['title']} ({t['level']})\n"
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=grammar_kb())

async def handle_grammar_answer(update, context):
    uid = update.effective_user.id
    s = sessions[uid]
    await update.message.chat.send_action(action="typing")
    result = check_grammar_answers(update.message.text, s["lesson"])
    await update.message.reply_text(result)
    # Спрашиваем — перейти к следующей теме?
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Следующая тема", callback_data="grammar_next")],
        [InlineKeyboardButton("🔁 Ещё раз эту", callback_data="grammar_retry")],
        [InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]
    ])
    await update.message.reply_text("Что дальше?", reply_markup=kb)

async def grammar_next_topic(update, context):
    q = update.callback_query
    uid = str(update.effective_user.id)
    await q.answer()
    cur = user_data[uid].get("grammar_progress", 0)
    user_data[uid]["grammar_progress"] = cur + 1
    save_json(USER_DATA_FILE, user_data)
    if uid in sessions:
        del sessions[uid]
    if cur + 1 >= len(grammar_topics):
        await q.edit_message_text("🎉 Ты прошёл все темы! Поздравляю!")
        return
    await q.edit_message_text(f"✅ Тема {cur + 1} завершена! Нажми «Новая тема» для следующей.", reply_markup=grammar_kb())

async def grammar_retry(update, context):
    q = update.callback_query
    uid = str(update.effective_user.id)
    await q.answer()
    if uid in sessions and sessions[uid].get("mode") == "grammar":
        lesson = sessions[uid]["lesson"]
        await q.message.reply_text("🔁 Повторяем урок:\n\n" + lesson)
        await q.message.reply_text("✍️ Напиши ответы на упражнения.")
    else:
        await q.edit_message_text("Сессия потеряна. Начни заново через /grammar.")

# === ПРОЧИЕ КОМАНДЫ (те, что уже были) ===
async def practice(update, context):
    target = update.callback_query.message if update.callback_query else update.message
    uid = update.effective_user.id
    if uid in sessions and sessions[uid].get("mode") == "learn":
        del sessions[uid]
    words_p = get_practice_words("main", 5)
    if not words_p:
        await target.reply_text("📝 Нет слов для практики. Сначала /learn.", reply_markup=main_kb())
        return
    await target.reply_text("⏳ Генерирую...")
    word_list = ", ".join(w["word"].split(" — ")[0] for w in words_p)
    prompt = f"Составь 3 предложения на английском с пропусками, используя: {word_list}. После каждого дай перевод на русский."
    try:
        r = deepseek_client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}], stream=False)
        cloze = r.choices[0].message.content
    except:
        cloze = "Ошибка"
    sessions[uid] = {"mode": "practice_cloze", "lesson": cloze}
    for part in split_text(f"📝 *Практика Cloze:*\n\n{cloze}", 4000):
        await target.reply_text(part, parse_mode="Markdown")
    await target.reply_text("✍️ Напиши ответы одним сообщением.", reply_markup=main_kb())

async def dialogue(update, context):
    target = update.callback_query.message if update.callback_query else update.message
    uid = update.effective_user.id
    if uid in sessions and sessions[uid].get("mode") == "learn":
        del sessions[uid]
    available = words_by_status("main", "mastered") + words_by_statuses("main", ["review_1", "review_2", "review_3"])
    if len(available) < 5:
        await target.reply_text(f"🗣️ Мало выученных слов ({len(available)}). Нужно 5+.", reply_markup=main_kb())
        return
    sel = random.sample(available, min(10, len(available)))
    await target.reply_text("🗣️ Генерирую диалог...")
    wl = ", ".join(w["word"].split(" — ")[0] for w in sel)
    prompt = f"Составь диалог на английском (6-8 реплик) с использованием: {wl}. После дай перевод."
    try:
        r = deepseek_client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}], stream=False)
        dlg = r.choices[0].message.content
    except:
        dlg = "Ошибка"
    for part in split_text(f"🗣️ *Диалог:*\n\n{dlg}", 4000):
        await target.reply_text(part, parse_mode="Markdown")
    await target.reply_text("📌 Прочитай вслух.", reply_markup=main_kb())

async def weak_words(update, context):
    error_words = [w for w in words if w.get("error_count", 0) > 0]
    target = update.callback_query.message if update.callback_query else update.message
    if not error_words:
        await target.reply_text("🔴 Ошибок нет!", reply_markup=more_kb())
        return
    sorted_w = sorted(error_words, key=lambda x: x.get("error_count", 0), reverse=True)[:10]
    text = "🔴 *Топ ошибок:*\n\n" + "\n".join(f"{i}. {w['word']} — {w['error_count']}" for i, w in enumerate(sorted_w, 1))
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=more_kb())
    else:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=more_kb())

async def progress(update, context):
    learned = words_by_statuses("main", ["learning", "review_1", "review_2", "review_3", "mastered"])
    if not learned:
        text = "📈 Нет данных."
    else:
        counts = defaultdict(int)
        for w in learned:
            if w.get("learned_at"):
                counts[w["learned_at"][:10]] += 1
        text = "📈 *Прогресс (30 дней):*\n\n"
        for d in sorted(counts.keys())[-30:]:
            bar = "█" * min(counts[d], 20)
            text += f"{d}: {bar} {counts[d]}\n"
    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=more_kb())
    else:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=more_kb())

async def reset_learning(update, context):
    cnt = 0
    for w in words:
        if w.get("status") == "learning":
            w["status"] = "new"
            w["learned_at"] = None
            cnt += 1
    save_json(WORDS_FILE, words)
    text = f"✅ Сброшено {cnt} слов в new."
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_kb())
    else:
        await update.message.reply_text(text, reply_markup=main_kb())

async def set_status(update, context):
    text = update.message.text
    r = detect_status_command(text)
    if not r:
        await update.message.reply_text("Формат: /status слово — status")
        return
    word_text, new_st = r
    valid = ["new", "learning", "review_1", "review_2", "review_3", "mastered"]
    if new_st not in valid:
        await update.message.reply_text(f"Статусы: {', '.join(valid)}")
        return
    if update_status("main", word_text, new_st):
        await update.message.reply_text(f"✅ {word_text} → {new_st}")
    else:
        await update.message.reply_text("❌ Не найдено.")

async def stop(update, context):
    uid = update.effective_user.id
    if uid in sessions:
        mode = sessions[uid].get("mode")
        del sessions[uid]
        await update.message.reply_text(f"⏹️ Сессия ({mode}) остановлена.", reply_markup=main_kb())
    else:
        await update.message.reply_text("ℹ️ Нет активной сессии.", reply_markup=main_kb())

async def toggle(update, context):
    uid = str(update.effective_user.id)
    if uid not in user_data:
        user_data[uid] = {}
    if "on" in update.message.text.lower():
        user_data[uid]["receives_daily"] = True
        save_json(USER_DATA_FILE, user_data)
        await update.message.reply_text("✅ Рассылка вкл.", reply_markup=main_kb())
    else:
        user_data[uid]["receives_daily"] = False
        save_json(USER_DATA_FILE, user_data)
        await update.message.reply_text("❌ Рассылка выкл.", reply_markup=main_kb())

# === ГЛАВНЫЙ ОБРАБОТЧИК ===
async def handle(update, context):
    uid = update.effective_user.id
    text = update.message.text

    # 1. Есть активная сессия?
    if uid in sessions:
        mode = sessions[uid].get("mode")
        if mode == "learn" and sessions[uid].get("waiting_for_answer"):
            await handle_learn_answer(update, context)
            return
        if mode == "translation":
            await handle_translation_answer(update, context)
            return
        if mode == "grammar":
            await handle_grammar_answer(update, context)
            return
        if mode == "practice_cloze":
            await update.message.chat.send_action(action="typing")
            res = check_grammar_answers(text, sessions[uid]["lesson"])
            await update.message.reply_text(res, reply_markup=main_kb())
            return

    # 2. Добавление слова
    add = detect_add_word(text)
    if add:
        if len(add) == 3:
            word, trans, direct_status = add
        else:
            word, trans = add
            direct_status = "new"
        existing = get_word_entry("main", word)
        if existing:
            await update.message.reply_text(f"⚠️ Уже есть: {existing['word']}")
            return
        new_w = {
            "word": f"{word} — {trans}",
            "status": direct_status,
            "added_at": datetime.now().isoformat(),
            "learned_at": datetime.now().isoformat() if direct_status == "learning" else None,
            "error_count": 0
        }
        words.append(new_w)
        save_json(WORDS_FILE, words)
        label = "в обучение" if direct_status == "learning" else "в новые"
        await update.message.reply_text(f"✅ Добавлено {label}: {new_w['word']}", reply_markup=main_kb())
        return

    # 3. Добавление фразового глагола
    pv_add = detect_phrasal_add(text)
    if pv_add:
        word, trans = pv_add
        existing = get_word_entry("phrasal", word)
        if existing:
            await update.message.reply_text(f"⚠️ Уже есть: {existing['word']}")
            return
        phrasal_verbs.append({
            "word": f"{word} — {trans}",
            "status": "new",
            "added_at": datetime.now().isoformat(),
            "learned_at": None,
            "error_count": 0
        })
        save_json(PHRASAL_FILE, phrasal_verbs)
        await update.message.reply_text(f"✅ ФГ добавлен: {word} — {trans}", reply_markup=main_kb())
        return

    # 4. Поиск перевода
    tr = None
    for w in words:
        full = w["word"]
        en, ru = full.split(" — ")
        if text.lower().strip() in (en.lower(), ru.lower()):
            tr = (en, ru)
            break
    if tr:
        await update.message.reply_text(f"📖 *{text}*\n🇬🇧 {tr[0]}\n🇷🇺 {tr[1]}", parse_mode="Markdown")
        return

    # 5. Много строк = переводы предложений
    if len(text.split('\n')) >= 2:
        await update.message.chat.send_action(action="typing")
        prompt = f"Проверь переводы:\n{text}\nОцени каждый, дай процент."
        try:
            r = deepseek_client.chat.completions.create(model="deepseek-chat", messages=[{"role": "user", "content": prompt}], stream=False)
            await update.message.reply_text(r.choices[0].message.content, reply_markup=main_kb())
        except:
            await update.message.reply_text("Не удалось проверить.", reply_markup=main_kb())
        return

    # 6. Обычный диалог
    await update.message.chat.send_action(action="typing")
    resp = ask_deepseek(uid, text)
    await update.message.reply_text(resp, reply_markup=main_kb())

# === ЗАПУСК ===
async def post_init(application):
    scheduler.start()

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        logging.error("❌ Нет токена")
        return

    request = HTTPXRequest(connection_pool_size=8, read_timeout=30, write_timeout=30, connect_timeout=30)
    app = Application.builder().token(token).request(request).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", lambda u, c: stats(u, c, "main")))
    app.add_handler(CommandHandler("progress", progress))
    app.add_handler(CommandHandler("weak", weak_words))
    app.add_handler(CommandHandler("daily_words", lambda u, c: daily_words(u, c, "main")))
    app.add_handler(CommandHandler("daily_phrasal", phrasal_daily))
    app.add_handler(CommandHandler("learn", lambda u, c: learn_start(u, c, "main")))
    app.add_handler(CommandHandler("learn_phrasal", lambda u, c: learn_start(u, c, "phrasal")))
    app.add_handler(CommandHandler("review", lambda u, c: learn_start(u, c, "main")))
    app.add_handler(CommandHandler("practice", practice))
    app.add_handler(CommandHandler("dialogue", dialogue))
    app.add_handler(CommandHandler("learning", lambda u, c: show_learning(u, c, "main")))
    app.add_handler(CommandHandler("status", set_status))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("on", toggle))
    app.add_handler(CommandHandler("off", toggle))
    app.add_handler(CommandHandler("reset_learning", reset_learning))
    app.add_handler(CommandHandler("phrasal_stats", lambda u, c: stats(u, c, "phrasal")))
    app.add_handler(CommandHandler("grammar", grammar_new))
    app.add_handler(CommandHandler("grammar_progress", grammar_progress))

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    scheduler.add_job(send_daily_tasks, CronTrigger(hour=9, minute=0), id='daily')

    print("✅ Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
