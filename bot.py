import os
import logging
import random
import json
import re
from datetime import datetime, timedelta, date
from collections import defaultdict
from openai import OpenAI
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import asyncio
from telegram.request import HTTPXRequest

load_dotenv()
logging.basicConfig(level=logging.INFO)

deepseek_client = OpenAI(
    api_key=os.environ.get('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com"
)

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

WORDS_FILE = "words.json"
PHRASAL_FILE = "phrasal.json"
GRAMMAR_FILE = "grammar.json"
STARTER_FILE = "c1_starter.json"
USER_DATA_FILE = "user_data.json"

def load_json(path, default):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

DEFAULT_GRAMMAR_TOPICS = [
    {"id": 1, "title": "Present Perfect vs Past Simple", "level": "B2"},
    {"id": 2, "title": "Past Perfect Continuous", "level": "B2"},
    {"id": 3, "title": "Future Perfect & Future Continuous", "level": "B2"},
    {"id": 4, "title": "Conditionals (Type 0, 1, 2, 3)", "level": "B2"},
    {"id": 5, "title": "Mixed Conditionals", "level": "B2"},
    {"id": 6, "title": "Passive Voice (all tenses)", "level": "B2"},
    {"id": 7, "title": "Reported Speech", "level": "B2"},
    {"id": 8, "title": "Modal Verbs of Deduction", "level": "B2"},
    {"id": 9, "title": "Gerund vs Infinitive", "level": "B2"},
    {"id": 10, "title": "Relative Clauses", "level": "B2"},
    {"id": 11, "title": "Inversion", "level": "C1"},
    {"id": 12, "title": "Cleft Sentences", "level": "C1"},
    {"id": 13, "title": "Participle Clauses", "level": "C1"},
    {"id": 14, "title": "Subjunctive & Unreal Past", "level": "C1"},
    {"id": 15, "title": "Ellipsis & Substitution", "level": "C1"}
]

def migrate_word(w):
    defaults = {
        "ease": 2.5, "interval": 0, "next_review": None,
        "reps": 0, "lapses": 0, "status": "new",
        "added_at": datetime.now().isoformat(),
        "learned_at": None, "error_count": 0,
        "saved_examples": [], "type": "word"
    }
    for k, v in defaults.items():
        if k not in w:
            w[k] = v
    if w.get("status") in ("review_1", "review_2", "review_3"):
        w["status"] = "review"
    return w

words = [migrate_word(w) for w in load_json(WORDS_FILE, [])]
phrasal_verbs = [migrate_word(w) for w in load_json(PHRASAL_FILE, [])]
grammar_topics = load_json(GRAMMAR_FILE, [])

starter_words = load_json(STARTER_FILE, [])
existing = {w["word"].split(" — ")[0].lower() for w in words}
added = 0
for sw in starter_words:
    w_text = sw["word"] if isinstance(sw, dict) else sw
    key = w_text.split(" — ")[0].lower()
    if key not in existing:
        words.append(migrate_word({"word": w_text, "status": "new"}))
        added += 1
if added:
    logging.info(f"Добавлено {added} стартовых C1-слов")

if not phrasal_verbs:
    phrasal_verbs = []
if not grammar_topics:
    grammar_topics = DEFAULT_GRAMMAR_TOPICS

for g in grammar_topics:
    for k, v in {"ease": 2.5, "interval": 0, "next_review": None,
                 "reps": 0, "lapses": 0, "status": "new"}.items():
        if k not in g:
            g[k] = v

user_data = load_json(USER_DATA_FILE, {})
for uid in list(user_data.keys()):
    d = user_data[uid]
    if "streak" not in d:
        d["streak"] = 0
        d["last_active"] = None
        d["daily_plan"] = {"words": 15, "phrasal": 5, "grammar": 1}
        d["new_today"] = {"date": None, "words": 0, "phrasal": 0}

user_histories = {}
sessions = {}
scheduler = AsyncIOScheduler()

# === SRS ===
def srs_update(item, quality):
    ease = item.get("ease", 2.5)
    interval = item.get("interval", 0)
    reps = item.get("reps", 0)
    lapses = item.get("lapses", 0)

    if quality == 0:
        reps = 0
        interval = 0
        ease = max(1.3, ease - 0.20)
        lapses += 1
        next_review = (datetime.now() + timedelta(minutes=10)).isoformat()
    else:
        if quality == 3:
            ease = max(1.3, ease - 0.15)
            mult = 1.2
        elif quality == 4:
            mult = ease
        else:
            ease = ease + 0.15
            mult = ease * 1.3

        if reps == 0:
            interval = 1
        elif reps == 1:
            interval = 3 if quality >= 4 else 2
        else:
            interval = max(1, round(interval * mult))
        reps += 1
        next_review = (datetime.now() + timedelta(days=interval)).isoformat()

    item["ease"] = round(ease, 2)
    item["interval"] = interval
    item["reps"] = reps
    item["lapses"] = lapses
    item["next_review"] = next_review
    if reps >= 1 and item.get("status") == "learning":
        item["status"] = "review"
    if reps >= 5 and lapses == 0:
        item["status"] = "mastered"
    return item

def is_due(item):
    if item.get("status") == "new":
        return False
    nr = item.get("next_review")
    if not nr:
        return True
    return datetime.fromisoformat(nr) <= datetime.now()

def due_items(track):
    pool = words if track == "main" else phrasal_verbs
    return [w for w in pool if is_due(w) and w.get("status") not in ("new",)]

def new_items(track, limit):
    pool = words if track == "main" else phrasal_verbs
    return [w for w in pool if w.get("status") == "new"][:limit]

def get_track(track):
    return words if track == "main" else phrasal_verbs

def save_track(track):
    if track == "main":
        save_json(WORDS_FILE, words)
    else:
        save_json(PHRASAL_FILE, phrasal_verbs)

def get_word_entry(track, text):
    for w in get_track(track):
        if w["word"].split(" — ")[0].lower() == text.lower():
            return w
    return None

def find_entry_anywhere(text):
    for track in ("main", "phrasal"):
        e = get_word_entry(track, text)
        if e:
            return track, e
    return None, None

def split_text(text, max_length=4000):
    if not text:
        return [""]
    if len(text) <= max_length:
        return [text]
    parts, current = [], ""
    for line in text.split('\n'):
        if len(current) + len(line) + 1 > max_length:
            parts.append(current)
            current = line
        else:
            current += '\n' + line if current else line
    if current:
        parts.append(current)
    return parts

def safe_ds(prompt, system=None):
    try:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        r = deepseek_client.chat.completions.create(
            model="deepseek-chat", messages=messages, stream=False
        )
        return r.choices[0].message.content
    except Exception as e:
        logging.error(f"DS: {e}")
        return None

def mark_activity(uid):
    uid = str(uid)
    if uid not in user_data:
        user_data[uid] = {}
    d = user_data[uid]
    today = date.today().isoformat()
    last = d.get("last_active")
    if last != today:
        if last:
            try:
                delta = (date.fromisoformat(today) - date.fromisoformat(last)).days
                d["streak"] = d.get("streak", 0) + 1 if delta == 1 else 1
            except:
                d["streak"] = 1
        else:
            d["streak"] = 1
        d["last_active"] = today
        d["new_today"] = {"date": today, "words": 0, "phrasal": 0}
        save_json(USER_DATA_FILE, user_data)

def get_plan(uid):
    return user_data.get(str(uid), {}).get("daily_plan", {"words": 15, "phrasal": 5, "grammar": 1})

def today_count(uid, key):
    uid = str(uid)
    nt = user_data.get(uid, {}).get("new_today", {})
    if nt.get("date") != date.today().isoformat():
        return 0
    return nt.get(key, 0)

def inc_today(uid, key, n=1):
    uid = str(uid)
    if uid not in user_data:
        user_data[uid] = {}
    today = date.today().isoformat()
    if "new_today" not in user_data[uid] or user_data[uid]["new_today"].get("date") != today:
        user_data[uid]["new_today"] = {"date": today, "words": 0, "phrasal": 0}
    user_data[uid]["new_today"][key] = user_data[uid]["new_today"].get(key, 0) + n
    save_json(USER_DATA_FILE, user_data)

def is_correct_answer(feedback):
    if not feedback:
        return False
    return "[CORRECT]" in feedback or "[ПРАВИЛЬНО]" in feedback

# === ГЕНЕРАЦИЯ ===
def gen_examples(items, count=2):
    if not items:
        return None
    wl = ", ".join(w["word"].split(" — ")[0] for w in items)
    prompt = f"""Для каждого слова/фразы напиши {count} примера на английском с переводом на русский. IT-сфера.
НЕ используй _ * [ ] ` в ответе.
Слова: {wl}
Формат:
Слово: [слово]
1. [англ] — [рус]
2. [англ] — [рус]
"""
    return safe_ds(prompt)

def gen_grammar_lesson(title, level):
    prompt = f"""Тема: "{title}" ({level}).
Составь: 1) правило 5-7 строк с примерами, 2) 5 упражнений с пропусками.
НЕ используй _ * [ ] `.
Формат:
Правило:
[текст]

Упражнения:
1. [с ______]
2. ...
"""
    return safe_ds(prompt)

def gen_reading(items):
    if not items:
        items = [{"word": "algorithm — алгоритм"}]
    wl = ", ".join(w["word"].split(" — ")[0] for w in items)
    prompt = f"""Составь короткий текст (150-250 слов, уровень B2-C1, IT-тематика),
включив эти слова/фразы: {wl}.
После текста — 3 вопроса на понимание.
НЕ используй _ * [ ] `.
Формат:
[текст]

Вопросы:
1. ...
2. ...
3. ...
"""
    return safe_ds(prompt)

def gen_output_task(item):
    w = item["word"].split(" — ")[0]
    prompt = f"""Дай короткое задание (1-2 строки) на использование слова '{w}' в рабочем предложении.
Просто попроси написать своё предложение. НЕ используй _ * [ ] `."""
    return safe_ds(prompt) or f"Напиши своё предложение со словом: {w}"

def check_output(user_text, word):
    prompt = f"""Пользователь написал своё предложение со словом '{word}':
{user_text}
Проверь грамматику и уместность. Если ок — похвали. Если ошибки — исправь.
Макс 4 строки. НЕ используй _ * [ ] `."""
    return safe_ds(prompt) or "Ок."

def gen_mnemonic(word, translation):
    prompt = f"""Придумай короткую мнемонику для запоминания английского слова.
Слово: {word}
Перевод: {translation}
Макс 3 строки. НЕ используй _ * [ ] `."""
    return safe_ds(prompt) or "Не удалось сгенерировать."

def check_translation(user_text, correct):
    prompt = f"""Оцени перевод.
ПРАВИЛЬНО: {correct}
ПОЛЬЗОВАТЕЛЬ: {user_text}
Ответь кратко: правильно или нет. Если ошибка — правильный вариант. Макс 3 строки.
В САМОМ КОНЦЕ поставь [CORRECT] или [WRONG].
НЕ используй _ * [ ] ` (кроме тега в конце)."""
    return safe_ds(prompt) or f"Правильно: {correct}\n[WRONG]"

# === КЛАВИАТУРЫ ===
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("☀️ Дневная сессия", callback_data="session_start")],
        [InlineKeyboardButton("🧠 Учить слова", callback_data="menu_learn")],
        [InlineKeyboardButton("🔤 Фразовые глаголы", callback_data="menu_phrasal")],
        [InlineKeyboardButton("📚 Грамматика B2-C1", callback_data="menu_grammar")],
        [InlineKeyboardButton("📖 Чтение", callback_data="menu_reading")],
        [InlineKeyboardButton("✍️ Output-практика", callback_data="menu_output")],
        [InlineKeyboardButton("📊 Прогресс и Streak", callback_data="menu_progress")],
        [InlineKeyboardButton("🔧 Ещё", callback_data="menu_more")]
    ])

def more_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Практика Cloze", callback_data="menu_practice")],
        [InlineKeyboardButton("🗣️ Диалог", callback_data="menu_dialogue")],
        [InlineKeyboardButton("🔴 Слабые (Leeches)", callback_data="menu_leeches")],
        [InlineKeyboardButton("🧪 Недельный тест", callback_data="menu_weekly")],
        [InlineKeyboardButton("🎙️ Говорение", callback_data="menu_speaking")],
        [InlineKeyboardButton("📈 Прогресс по дням", callback_data="menu_daily_progress")],
        [InlineKeyboardButton("⚙️ План дня", callback_data="menu_plan")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_back")]
    ])

def phrasal_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Взять новые ФГ", callback_data="pv_daily")],
        [InlineKeyboardButton("🧠 Учить ФГ (SRS)", callback_data="pv_learn")],
        [InlineKeyboardButton("📊 Статистика ФГ", callback_data="pv_stats")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_back")]
    ])

def grammar_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Новая тема", callback_data="grammar_new")],
        [InlineKeyboardButton("🔄 Повторить due-темы", callback_data="grammar_due")],
        [InlineKeyboardButton("📊 Прогресс грамматики", callback_data="grammar_progress")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_back")]
    ])

def srs_kb(uid, idx, track):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 Again", callback_data=f"srs_a_{uid}_{idx}_{track}")],
        [InlineKeyboardButton("🟠 Hard", callback_data=f"srs_h_{uid}_{idx}_{track}"),
         InlineKeyboardButton("🟢 Good", callback_data=f"srs_g_{uid}_{idx}_{track}")],
        [InlineKeyboardButton("🔵 Easy", callback_data=f"srs_e_{uid}_{idx}_{track}")],
        [InlineKeyboardButton("⏹️ Закончить", callback_data=f"srs_stop_{uid}")]
    ])

def plan_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("15/5/1 (по умолчанию)", callback_data="plan_15_5_1")],
        [InlineKeyboardButton("20/8/1", callback_data="plan_20_8_1")],
        [InlineKeyboardButton("10/3/1 (легко)", callback_data="plan_10_3_1")],
        [InlineKeyboardButton("25/10/2 (C1 интенсив)", callback_data="plan_25_10_2")],
        [InlineKeyboardButton("◀️ Назад", callback_data="menu_more")]
    ])

# === /START ===
async def start(update, context):
    uid = str(update.effective_user.id)
    if uid not in user_data:
        user_data[uid] = {"receives_daily": True, "streak": 0, "last_active": None,
                          "daily_plan": {"words": 15, "phrasal": 5, "grammar": 1},
                          "new_today": {"date": None, "words": 0, "phrasal": 0}}
        save_json(USER_DATA_FILE, user_data)
    mark_activity(uid)
    d = user_data[uid]
    due_main = len(due_items("main"))
    due_pv = len(due_items("phrasal"))
    text = ("🎓 Твой C1-тренер\n\n"
            f"🔥 Streak: {d.get('streak', 0)} дней\n"
            f"📥 Новых слов: {len([w for w in words if w['status']=='new'])}\n"
            f"🔄 Due-повторений (слова): {due_main}\n"
            f"🔄 Due-повторений (ФГ): {due_pv}\n"
            f"✅ Выучено: {len([w for w in words if w['status']=='mastered'])}\n\n"
            "Жми «☀️ Дневная сессия» — 30 минут, всё по плану.")
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_kb())
    else:
        await update.message.reply_text(text, reply_markup=main_kb())

# === ДНЕВНАЯ СЕССИЯ ===
async def session_start(update, context):
    q = update.callback_query
    uid = q.from_user.id
    mark_activity(uid)
    if uid in sessions:
        del sessions[uid]
    plan = get_plan(uid)

    words_taken = today_count(uid, "words")
    pv_taken = today_count(uid, "phrasal")
    new_w_limit = max(0, plan["words"] - words_taken)
    new_p_limit = max(0, plan["phrasal"] - pv_taken)

    due_w = due_items("main")
    due_p = due_items("phrasal")
    new_w = new_items("main", new_w_limit)
    new_p = new_items("phrasal", new_p_limit)

    due_g = [g for g in grammar_topics if g.get("status") != "new" and is_due(g)]
    new_g = [g for g in grammar_topics if g.get("status") == "new"]

    queue = []
    for w in due_w:
        queue.append({"kind": "srs", "track": "main", "item": w, "is_new": False})
    for w in due_p:
        queue.append({"kind": "srs", "track": "phrasal", "item": w, "is_new": False})
    for w in new_w:
        w["status"] = "learning"
        w["learned_at"] = datetime.now().isoformat()
        queue.append({"kind": "srs", "track": "main", "item": w, "is_new": True})
    for w in new_p:
        w["status"] = "learning"
        w["learned_at"] = datetime.now().isoformat()
        queue.append({"kind": "srs", "track": "phrasal", "item": w, "is_new": True})
    save_track("main")
    save_track("phrasal")

    random.shuffle(queue)

    sessions[uid] = {
        "mode": "session",
        "queue": queue,
        "idx": 0,
        "phase": "srs",
        "track": None,
        "current_correct": "",
        "grammar_item": (due_g[0] if due_g else (new_g[0] if new_g and plan["grammar"] > 0 else None)),
        "grammar_lesson": None,
        "reading_text": None,
        "output_item": None,
        "waiting": False,
        "results": {"again": 0, "hard": 0, "good": 0, "easy": 0},
        "new_completed": {"main": 0, "phrasal": 0},
        "new_total": {"main": len(new_w), "phrasal": len(new_p)}
    }

    total = len(queue)
    await q.message.reply_text(
        f"☀️ Дневная сессия\n\n"
        f"🔄 Повторений (слова): {len(due_w)}\n"
        f"🔄 Повторений (ФГ): {len(due_p)}\n"
        f"➕ Новых слов: {len(new_w)}\n"
        f"➕ Новых ФГ: {len(new_p)}\n"
        f"{'📚 Грамматика: ' + (sessions[uid]['grammar_item']['title'] if sessions[uid]['grammar_item'] else 'нет')}\n\n"
        f"Начинаем! Всего SRS-карточек: {total}"
    )
    await session_next(update, uid)

async def session_next(update, uid):
    if uid not in sessions:
        return
    s = sessions[uid]
    if s["phase"] == "srs":
        if s["idx"] >= len(s["queue"]):
            s["phase"] = "grammar"
            await session_grammar(update, uid)
            return
        item = s["queue"][s["idx"]]
        s["track"] = item["track"]
        w = item["item"]
        text = w["word"]
        en, ru = (text.split(" — ") + [""])[:2]
        s["current_correct"] = en
        s["waiting"] = True
        head = f"[{s['idx']+1}/{len(s['queue'])}] {'Слово' if item['track']=='main' else 'ФГ'}"

        # НОВОЕ: показываем слово + перевод + пример
        if item.get("is_new") and not item.get("presented"):
            item["presented"] = True
            if not w.get("saved_examples"):
                ex = gen_examples([w], 2)
                if ex:
                    w["saved_examples"] = [ex]
                    save_track(item["track"])
            msg = f"🆕 {head} — НОВОЕ\n\n📖 {w['word']}"
            if w.get("saved_examples"):
                msg += f"\n\n📝 Пример:\n{w['saved_examples'][0]}"
            msg += "\n\n✍️ Напиши перевод на английский (посмотри выше):"
            await update.effective_message.reply_text(msg)
            return

        # Старое: только русский, вспоминаем
        ctx = ""
        if w.get("saved_examples"):
            ctx = f"\n\n📎 Пример:\n{w['saved_examples'][0]}"
        await update.effective_message.reply_text(
            f"{head}\n\n📖 {ru}{ctx}\n\n✍️ Напиши перевод на английский:"
        )
        return

    if s["phase"] == "grammar":
        await session_grammar(update, uid)
        return
    if s["phase"] == "reading":
        await session_reading(update, uid)
        return
    if s["phase"] == "output":
        await session_output(update, uid)
        return
    if s["phase"] == "done":
        await session_finish(update, uid)
        return

async def session_grammar(update, uid):
    s = sessions[uid]
    item = s.get("grammar_item")
    if not item:
        s["phase"] = "reading"
        await session_reading(update, uid)
        return
    msg = update.effective_message
    await msg.reply_text(f"⏳ Грамматика: {item['title']}...")
    lesson = gen_grammar_lesson(item["title"], item["level"])
    if not lesson:
        s["phase"] = "reading"
        await session_reading(update, uid)
        return
    s["grammar_lesson"] = lesson
    s["waiting"] = "grammar"
    for part in split_text(f"📚 {item['title']}\n\n{lesson}", 4000):
        await msg.reply_text(part)
    await msg.reply_text("✍️ Напиши ответы на упражнения одним сообщением.")

async def session_reading(update, uid):
    s = sessions[uid]
    pool = [w for w in words if w["status"] in ("learning", "review")]
    if not pool:
        pool = words[:5] if words else [{"word": "algorithm — алгоритм"}]
    sel = random.sample(pool, min(5, len(pool)))
    msg = update.effective_message
    await msg.reply_text("⏳ Готовлю текст для чтения...")
    text = gen_reading(sel)
    if not text:
        s["phase"] = "output"
        await session_output(update, uid)
        return
    s["reading_text"] = text
    s["waiting"] = "reading"
    for part in split_text(f"📖 Чтение:\n\n{text}", 4000):
        await msg.reply_text(part)
    await msg.reply_text("✍️ Ответь на 3 вопроса одним сообщением.")

async def session_output(update, uid):
    s = sessions[uid]
    pool = [w for w in words if w["status"] in ("review", "mastered")]
    if not pool:
        s["phase"] = "done"
        await session_finish(update, uid)
        return
    item = random.choice(pool)
    s["output_item"] = item
    s["waiting"] = "output"
    task = gen_output_task(item)
    await update.effective_message.reply_text(f"✍️ Output:\n\n{task}")

async def session_finish(update, uid):
    if uid not in sessions:
        return
    s = sessions[uid]
    r = s["results"]
    nc = s.get("new_completed", {"main": 0, "phrasal": 0})
    if nc["main"] > 0:
        inc_today(uid, "words", nc["main"])
    if nc["phrasal"] > 0:
        inc_today(uid, "phrasal", nc["phrasal"])

    text = (f"🎉 Дневная сессия завершена!\n\n"
            f"🔴 Again: {r['again']}\n"
            f"🟠 Hard: {r['hard']}\n"
            f"🟢 Good: {r['good']}\n"
            f"🔵 Easy: {r['easy']}\n\n"
            f"🔥 Streak: {user_data.get(str(uid), {}).get('streak', 0)}")
    del sessions[uid]
    await update.effective_message.reply_text(text, reply_markup=main_kb())

async def handle_session_answer(update, context):
    uid = update.effective_user.id
    s = sessions[uid]
    txt = update.message.text

    if s["phase"] == "srs" and s.get("waiting") is True:
        item = s["queue"][s["idx"]]
        feedback = check_translation(txt, s["current_correct"])
        await update.message.reply_text(feedback)
        s["waiting"] = False
        kb = srs_kb(uid, s["idx"], item["track"])
        await update.message.reply_text("Оцени себя:", reply_markup=kb)
        return

    if s["waiting"] == "grammar":
        result = safe_ds(f"Урок:\n{s['grammar_lesson']}\n\nОтветы:\n{txt}\n\nПроверь и укажи ошибки. Макс 8 строк. НЕ используй _ * [ ] `.")
        await update.message.reply_text(result or "Ок.")
        gi = s["grammar_item"]
        if gi:
            for g in grammar_topics:
                if g["id"] == gi["id"]:
                    srs_update(g, 4)
                    break
            save_json(GRAMMAR_FILE, grammar_topics)
        s["waiting"] = False
        s["phase"] = "reading"
        await session_reading(update, uid)
        return

    if s["waiting"] == "reading":
        result = safe_ds(f"Текст:\n{s['reading_text']}\n\nОтветы пользователя:\n{txt}\n\nОцени ответы. Макс 6 строк. НЕ используй _ * [ ] `.")
        await update.message.reply_text(result or "Ок.")
        s["waiting"] = False
        s["phase"] = "output"
        await session_output(update, uid)
        return

    if s["waiting"] == "output":
        item = s["output_item"]
        w_text = item["word"].split(" — ")[0]
        result = check_output(txt, w_text)
        await update.message.reply_text(result)
        s["waiting"] = False
        s["phase"] = "done"
        await session_finish(update, uid)
        return

# === SRS КНОПКИ (ФИКС ПАРСИНГА) ===
async def handle_srs_button(update, context):
    q = update.callback_query
    data = q.data
    parts = data.split("_")
    uid = q.from_user.id

    if len(parts) < 5:
        await q.answer("Некорректная кнопка")
        return

    if uid not in sessions or sessions[uid].get("mode") != "session":
        await q.answer("Сессия не активна")
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except:
            pass
        return

    s = sessions[uid]

    if parts[1] == "stop":
        await q.answer("Завершаем")
        s["phase"] = "done"
        await session_finish(update, uid)
        return

    quality_map = {"a": 0, "h": 3, "g": 4, "e": 5}
    q_val = quality_map.get(parts[1])
    if q_val is None:
        await q.answer("Ошибка кнопки")
        return
    try:
        idx = int(parts[3])
    except ValueError:
        await q.answer("Ошибка индекса")
        return
    track = parts[4]

    if idx != s["idx"]:
        await q.answer("Эта карточка уже обработана")
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except:
            pass
        return

    item = s["queue"][idx]
    w_obj = item["item"]

    await q.answer("✅ Сохранено")

    old_examples = w_obj.get("saved_examples", [])
    srs_update(w_obj, q_val)
    if q_val >= 4 and not old_examples:
        ex = gen_examples([w_obj], 2)
        if ex:
            w_obj["saved_examples"] = [ex]
    if item.get("is_new") and q_val >= 3:
        s["new_completed"][track] = s["new_completed"].get(track, 0) + 1
    save_track(track)

    key = {0: "again", 3: "hard", 4: "good", 5: "easy"}[q_val]
    s["results"][key] += 1
    s["idx"] += 1

    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except:
        pass

    await session_next(update, uid)

# === ОБУЧЕНИЕ ===
async def learn_start(update, context, track="main"):
    q = update.callback_query
    uid = q.from_user.id if q else update.effective_user.id
    if uid in sessions:
        del sessions[uid]
    due = due_items(track)
    new = new_items(track, 10)
    queue = []
    for w in due:
        queue.append({"kind": "srs", "track": track, "item": w, "is_new": False})
    for w in new:
        w["status"] = "learning"
        w["learned_at"] = datetime.now().isoformat()
        queue.append({"kind": "srs", "track": track, "item": w, "is_new": True})
    save_track(track)
    if not queue:
        msg = "Нет слов для тренировки."
        if q:
            await q.message.reply_text(msg, reply_markup=main_kb())
        else:
            await update.message.reply_text(msg, reply_markup=main_kb())
        return
    random.shuffle(queue)
    sessions[uid] = {
        "mode": "session", "queue": queue, "idx": 0, "phase": "srs",
        "track": track, "current_correct": "", "waiting": True,
        "results": {"again": 0, "hard": 0, "good": 0, "easy": 0},
        "new_completed": {"main": 0, "phrasal": 0},
        "grammar_item": None, "grammar_lesson": None,
        "reading_text": None, "output_item": None
    }
    target = q.message if q else update.message
    await target.reply_text(f"🧠 SRS-тренировка ({'ФГ' if track=='phrasal' else 'слова'}). {len(queue)} карточек.")
    await session_next(update, uid)

# === ФРАЗОВЫЕ ГЛАГОЛЫ ===
async def phrasal_daily(update, context):
    q = update.callback_query
    uid = q.from_user.id
    plan = get_plan(uid)
    taken = today_count(uid, "phrasal")
    limit = max(0, plan["phrasal"] - taken)
    new = new_items("phrasal", limit)
    if not new:
        await q.message.reply_text("🎉 ФГ новых нет или достигнут лимит.", reply_markup=main_kb())
        return
    for w in new:
        w["status"] = "learning"
        w["learned_at"] = datetime.now().isoformat()
    save_track("phrasal")
    inc_today(uid, "phrasal", len(new))
    await q.message.reply_text("🧠 Примеры...")
    ex = gen_examples(new, 2)
    text = f"📚 Взято {len(new)} ФГ:\n\n"
    for i, w in enumerate(new, 1):
        text += f"{i}. {w['word']}\n"
    if ex:
        text += f"\n📝 {ex}"
    for p in split_text(text, 4000):
        await q.message.reply_text(p)
    await q.message.reply_text("Дальше: /learn_phrasal или кнопка Учить ФГ", reply_markup=main_kb())

# === ГРАММАТИКА ===
async def grammar_new(update, context):
    q = update.callback_query
    uid = q.from_user.id if q else update.effective_user.id
    new_topics = [g for g in grammar_topics if g.get("status") == "new"]
    if not new_topics:
        target = q.message if q else update.message
        await target.reply_text("🎉 Все темы пройдены!", reply_markup=grammar_kb())
        return
    topic = new_topics[0]
    target = q.message if q else update.message
    await target.reply_text(f"⏳ Готовлю: {topic['title']}...")
    lesson = gen_grammar_lesson(topic["title"], topic["level"])
    if not lesson:
        await target.reply_text("Не удалось. Попробуй позже.")
        return
    sessions[uid] = {"mode": "grammar_lesson", "topic_id": topic["id"], "lesson": lesson}
    for p in split_text(f"📚 {topic['title']}\n\n{lesson}", 4000):
        await target.reply_text(p)
    await target.reply_text("✍️ Ответь одним сообщением.")

async def grammar_due(update, context):
    q = update.callback_query
    uid = q.from_user.id
    due = [g for g in grammar_topics if g.get("status") != "new" and is_due(g)]
    if not due:
        await q.message.reply_text("Нет due-тем.", reply_markup=grammar_kb())
        return
    topic = due[0]
    await q.message.reply_text(f"⏳ Повтор: {topic['title']}...")
    lesson = gen_grammar_lesson(topic["title"], topic["level"])
    if not lesson:
        await q.message.reply_text("Не удалось.")
        return
    sessions[uid] = {"mode": "grammar_lesson", "topic_id": topic["id"], "lesson": lesson}
    for p in split_text(f"🔄 {topic['title']} (повтор)\n\n{lesson}", 4000):
        await q.message.reply_text(p)
    await q.message.reply_text("✍️ Ответь одним сообщением.")

async def grammar_progress(update, context):
    q = update.callback_query
    done = len([g for g in grammar_topics if g.get("status") != "new"])
    text = f"📚 Пройдено: {done}/{len(grammar_topics)}\n\n"
    for i, g in enumerate(grammar_topics, 1):
        mark = "✅" if g.get("status") == "mastered" else ("🔄" if g.get("status") == "review" else "⏳")
        nr = g.get("next_review")
        extra = f" (next: {nr[:10]})" if nr else ""
        text += f"{mark} {i}. {g['title']}{extra}\n"
    await q.edit_message_text(text, reply_markup=grammar_kb())

async def handle_grammar_lesson_answer(update, context):
    uid = update.effective_user.id
    s = sessions[uid]
    result = safe_ds(f"Урок:\n{s['lesson']}\n\nОтветы:\n{update.message.text}\n\nПроверь. Макс 8 строк. НЕ используй _ * [ ] `.")
    await update.message.reply_text(result or "Ок.")
    tid = s["topic_id"]
    for g in grammar_topics:
        if g["id"] == tid:
            srs_update(g, 4)
            break
    save_json(GRAMMAR_FILE, grammar_topics)
    del sessions[uid]
    user_data[str(uid)]["last_grammar_topic"] = tid
    save_json(USER_DATA_FILE, user_data)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Следующая тема", callback_data="grammar_next")],
        [InlineKeyboardButton("🔁 Ещё раз эту", callback_data="grammar_retry")],
        [InlineKeyboardButton("🏠 Меню", callback_data="menu_back")]
    ])
    await update.message.reply_text("Что дальше?", reply_markup=kb)

async def grammar_next_topic(update, context):
    q = update.callback_query
    await q.answer()
    return await grammar_new(update, context)

async def grammar_retry(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    tid = user_data.get(str(uid), {}).get("last_grammar_topic")
    if not tid:
        await q.message.reply_text("Не удалось найти тему. /grammar")
        return
    topic = next((g for g in grammar_topics if g["id"] == tid), None)
    if not topic:
        await q.message.reply_text("Тема не найдена.")
        return
    await q.message.reply_text(f"⏳ Готовлю: {topic['title']}...")
    lesson = gen_grammar_lesson(topic["title"], topic["level"])
    if not lesson:
        await q.message.reply_text("Не удалось.")
        return
    sessions[uid] = {"mode": "grammar_lesson", "topic_id": tid, "lesson": lesson}
    for p in split_text(f"🔁 {topic['title']} (повтор)\n\n{lesson}", 4000):
        await q.message.reply_text(p)
    await q.message.reply_text("✍️ Ответь одним сообщением.")

# === ЧТЕНИЕ ===
async def reading_start(update, context):
    q = update.callback_query
    uid = q.from_user.id
    pool = [w for w in words if w["status"] in ("learning", "review")]
    if not pool:
        pool = words[:5] if words else [{"word": "algorithm — алгоритм"}]
    sel = random.sample(pool, min(5, len(pool)))
    await q.message.reply_text("⏳ Текст...")
    text = gen_reading(sel)
    if not text:
        await q.message.reply_text("Не удалось.")
        return
    sessions[uid] = {"mode": "reading", "text": text}
    for p in split_text(f"📖 Чтение:\n\n{text}", 4000):
        await q.message.reply_text(p)
    await q.message.reply_text("✍️ Ответь на вопросы одним сообщением.")

async def handle_reading_answer(update, context):
    uid = update.effective_user.id
    s = sessions[uid]
    result = safe_ds(f"Текст:\n{s['text']}\n\nОтветы:\n{update.message.text}\n\nОцени. Макс 6 строк. НЕ используй _ * [ ] `.")
    await update.message.reply_text(result or "Ок.")
    del sessions[uid]

# === OUTPUT ===
async def output_start(update, context):
    q = update.callback_query
    uid = q.from_user.id
    pool = [w for w in words if w["status"] in ("review", "mastered")]
    if not pool:
        await q.message.reply_text("Нет слов в review. Сначала выучи.", reply_markup=main_kb())
        return
    item = random.choice(pool)
    sessions[uid] = {"mode": "output", "item": item}
    task = gen_output_task(item)
    await q.message.reply_text(f"✍️ {task}")

async def handle_output_answer(update, context):
    uid = update.effective_user.id
    s = sessions[uid]
    w = s["item"]["word"].split(" — ")[0]
    result = check_output(update.message.text, w)
    await update.message.reply_text(result, reply_markup=main_kb())
    del sessions[uid]

# === ПРОГРЕСС ===
async def progress_menu(update, context):
    q = update.callback_query
    uid = str(q.from_user.id)
    mark_activity(uid)
    d = user_data[uid]
    mastered = len([w for w in words if w["status"] == "mastered"])
    review = len([w for w in words if w["status"] == "review"])
    learning = len([w for w in words if w["status"] == "learning"])
    new = len([w for w in words if w["status"] == "new"])
    pv_mastered = len([w for w in phrasal_verbs if w["status"] == "mastered"])
    text = (f"📊 Прогресс\n\n"
            f"🔥 Streak: {d.get('streak', 0)}\n"
            f"📥 Новых: {new}\n"
            f"📖 Учу: {learning}\n"
            f"🔄 Review: {review}\n"
            f"✅ Mastered: {mastered}\n"
            f"📚 Всего слов: {len(words)}\n"
            f"🔤 ФГ mastered: {pv_mastered}/{len(phrasal_verbs)}\n\n"
            f"Milestones: {'🎯 100' if mastered >= 100 else ''}"
            f"{' 🏆 500' if mastered >= 500 else ''}"
            f"{' 💎 1000' if mastered >= 1000 else ''}")
    await q.edit_message_text(text, reply_markup=main_kb())

async def daily_progress(update, context):
    q = update.callback_query
    counts = defaultdict(int)
    for w in words:
        if w.get("learned_at"):
            d = w["learned_at"][:10]
            counts[d] += 1
    if not counts:
        await q.edit_message_text("📈 Данных пока нет.", reply_markup=more_kb())
        return
    text = "📈 По дням (последние 14):\n\n"
    for k in sorted(counts.keys())[-14:]:
        text += f"{k}: выучено {counts[k]}\n"
    await q.edit_message_text(text, reply_markup=more_kb())

# === LEECHES ===
async def leeches(update, context):
    q = update.callback_query
    uid = q.from_user.id
    pool = [w for w in words if w.get("lapses", 0) >= 8]
    if not pool:
        await q.edit_message_text("Leeches нет. Отлично!", reply_markup=more_kb())
        return
    sessions[uid] = {"mode": "leeches", "pool": pool}
    text = f"🩸 Leeches ({len(pool)}):\n\n"
    for w in pool[:15]:
        text += f"• {w['word']} (lapses: {w['lapses']})\n"
    text += "\nНажми на слово, чтобы получить мнемонику."
    kb_rows = [[InlineKeyboardButton(f"🧠 {w['word'][:40]}", callback_data=f"mn_{i}")]
               for i, w in enumerate(pool[:10])]
    kb_rows.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_more")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb_rows))

async def mnemonic(update, context):
    q = update.callback_query
    await q.answer("⏳")
    uid = q.from_user.id
    if uid not in sessions or sessions[uid].get("mode") != "leeches":
        await q.message.reply_text("Список устарел. Открой Leeches заново.")
        return
    idx = int(q.data.split("_")[1])
    pool = sessions[uid]["pool"]
    if idx >= len(pool):
        return
    w = pool[idx]
    en, ru = w["word"].split(" — ")
    mn = gen_mnemonic(en, ru)
    await q.message.reply_text(f"🩸 {w['word']}\n\n🧠 {mn}")

# === НЕДЕЛЬНЫЙ ТЕСТ ===
async def weekly_test(update, context):
    q = update.callback_query
    uid = q.from_user.id
    pool_review = [w for w in words if w["status"] in ("review", "mastered")]
    if len(pool_review) < 10:
        await q.message.reply_text("Нужно 10+ выученных слов.", reply_markup=more_kb())
        return
    sel = random.sample(pool_review, min(20, len(pool_review)))
    sessions[uid] = {"mode": "weekly", "queue": sel, "idx": 0, "correct": 0}
    await q.message.reply_text(f"🧪 Недельный тест: {len(sel)} слов. RU→EN.\n/stop — прервать.")
    await weekly_next(update, uid)

async def weekly_next(update, uid):
    s = sessions[uid]
    if s["idx"] >= len(s["queue"]):
        r = f"🧪 Тест завершён!\n✅ {s['correct']}/{len(s['queue'])}"
        del sessions[uid]
        await update.effective_message.reply_text(r, reply_markup=main_kb())
        return
    w = s["queue"][s["idx"]]
    en, ru = w["word"].split(" — ")
    s["current"] = en
    await update.effective_message.reply_text(f"[{s['idx']+1}/{len(s['queue'])}] {ru}\n\nПеревод:")

async def handle_weekly_answer(update, context):
    uid = update.effective_user.id
    s = sessions[uid]
    fb = check_translation(update.message.text, s["current"])
    if is_correct_answer(fb):
        s["correct"] += 1
    await update.message.reply_text(fb)
    s["idx"] += 1
    await weekly_next(update, uid)

# === SPEAKING ===
async def speaking(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not openai_client:
        await q.edit_message_text(
            "🎙️ Для голосовых нужен OPENAI_API_KEY.\nДобавь его в Render.",
            reply_markup=more_kb()
        )
        return
    prompt = "Tell me about a time you had to pivot at work. Speak 1-2 minutes."
    sessions[uid] = {"mode": "speaking_prompt"}
    await q.message.reply_text(f"🎙️ {prompt}\n\nЗапиши голосовое.")

async def handle_voice(update, context):
    uid = update.effective_user.id
    if uid not in sessions or sessions[uid].get("mode") != "speaking_prompt":
        return
    if not openai_client:
        return
    await update.message.reply_text("⏳ Распознаю...")
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    path = f"/tmp/voice_{uid}.ogg"
    await file.download_to_drive(path)
    transcript = ""
    try:
        with open(path, "rb") as f:
            tr = openai_client.audio.transcriptions.create(model="whisper-1", file=f)
        transcript = tr.text
    except Exception as e:
        logging.error(f"Whisper: {e}")
        await update.message.reply_text("Не удалось распознать.")
        return
    finally:
        try:
            os.remove(path)
        except:
            pass
    feedback = safe_ds(f"Студент сказал:\n{transcript}\n\nОцени беглость, грамматику, лексику. Макс 6 строк. НЕ используй _ * [ ] `.")
    await update.message.reply_text(f"📝 Расшифровка:\n{transcript}\n\n🎯 Оценка:\n{feedback or 'Ок'}")
    del sessions[uid]

# === ПЛАН ===
async def plan_menu(update, context):
    q = update.callback_query
    uid = str(q.from_user.id)
    p = get_plan(uid)
    await q.edit_message_text(
        f"⚙️ План: {p['words']} слов, {p['phrasal']} ФГ, {p['grammar']} грамматика.\n\nВыбери:",
        reply_markup=plan_kb()
    )

async def plan_set(update, context):
    q = update.callback_query
    uid = str(q.from_user.id)
    _, w, p, g = q.data.split("_")
    user_data[uid]["daily_plan"] = {"words": int(w), "phrasal": int(p), "grammar": int(g)}
    save_json(USER_DATA_FILE, user_data)
    await q.edit_message_text(f"✅ План: {w} слов, {p} ФГ, {g} грамматика.", reply_markup=main_kb())

# === CLOZE / DIALOGUE ===
async def practice_cloze(update, context):
    q = update.callback_query
    uid = q.from_user.id
    pool = [w for w in words if w["status"] in ("review", "mastered")]
    if not pool:
        await q.message.reply_text("Мало слов.", reply_markup=more_kb())
        return
    sel = random.sample(pool, min(5, len(pool)))
    wl = ", ".join(w["word"].split(" — ")[0] for w in sel)
    cloze = safe_ds(f"Составь 3 предложения с пропусками. Используй: {wl}. После — перевод. НЕ используй _ * [ ] `.") or "Ошибка"
    sessions[uid] = {"mode": "practice_cloze", "lesson": cloze}
    for p in split_text(f"📝 Cloze:\n\n{cloze}", 4000):
        await q.message.reply_text(p)
    await q.message.reply_text("Напиши ответы.")

async def dialogue_start(update, context):
    q = update.callback_query
    pool = [w for w in words if w["status"] in ("review", "mastered")]
    if len(pool) < 5:
        await q.message.reply_text("Мало слов.", reply_markup=more_kb())
        return
    sel = random.sample(pool, min(10, len(pool)))
    wl = ", ".join(w["word"].split(" — ")[0] for w in sel)
    dlg = safe_ds(f"Диалог на английском (6-8 реплик) с: {wl}. После перевод. НЕ используй _ * [ ] `.") or "Ошибка"
    for p in split_text(f"🗣️ Диалог:\n\n{dlg}", 4000):
        await q.message.reply_text(p)

# === СТАТУС/СБРОС ===
async def set_status_cmd(update, context):
    m = re.search(r'/status\s+(.+?)\s+[-—]\s+(\w+)', update.message.text, re.IGNORECASE)
    if not m:
        await update.message.reply_text("/status слово — status")
        return
    word, st = m.group(1).strip(), m.group(2).lower()
    valid = ["new", "learning", "review", "mastered"]
    if st not in valid:
        await update.message.reply_text(f"Статусы: {', '.join(valid)}")
        return
    track, entry = find_entry_anywhere(word)
    if not entry:
        await update.message.reply_text("Не найдено.")
        return
    entry["status"] = st
    if st == "new":
        entry["reps"] = 0
        entry["interval"] = 0
        entry["next_review"] = None
    elif st == "review":
        entry["next_review"] = (datetime.now() + timedelta(days=3)).isoformat()
    save_track(track)
    await update.message.reply_text(f"✅ {entry['word']} → {st}")

async def reset_learning(update, context):
    cnt = 0
    for w in words:
        if w["status"] == "learning":
            w["status"] = "new"
            w["learned_at"] = None
            cnt += 1
    save_json(WORDS_FILE, words)
    if update.callback_query:
        await update.callback_query.edit_message_text(f"✅ Сброшено {cnt}.", reply_markup=main_kb())
    else:
        await update.message.reply_text(f"✅ Сброшено {cnt}.")

async def stop_cmd(update, context):
    uid = update.effective_user.id
    if uid in sessions:
        mode = sessions[uid].get("mode")
        del sessions[uid]
        await update.message.reply_text(f"⏹️ Сессия ({mode}) прервана.", reply_markup=main_kb())
    else:
        await update.message.reply_text("Нет сессии.", reply_markup=main_kb())

async def toggle_sub(update, context):
    uid = str(update.effective_user.id)
    if uid not in user_data:
        user_data[uid] = {}
    on = "on" in update.message.text.lower()
    user_data[uid]["receives_daily"] = on
    save_json(USER_DATA_FILE, user_data)
    await update.message.reply_text("✅ Вкл" if on else "❌ Выкл", reply_markup=main_kb())

# === ДЕТЕКТОРЫ ===
def detect_add(text):
    m = re.search(r'^\+ (.+?)\s*[-—]\s*(.+)$', text.strip())
    if m:
        return (m.group(1).strip(), m.group(2).strip(), "learning")
    m = re.search(r'добавь\s+слово\s+(.+?)\s*[-—]\s*(.+)', text, re.IGNORECASE)
    if m:
        return (m.group(1).strip(), m.group(2).strip(), "new")
    return None

def detect_pv_add(text):
    m = re.search(r'^фг\s+(.+?)\s*[-—]\s*(.+)$', text.strip(), re.IGNORECASE)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    return None

# === ГЛАВНЫЙ HANDLE ===
async def handle(update, context):
    if update.message.voice:
        return await handle_voice(update, context)

    uid = update.effective_user.id
    text = update.message.text

    if uid in sessions:
        mode = sessions[uid].get("mode")
        if mode == "session":
            w = sessions[uid].get("waiting")
            if w is True or isinstance(w, str):
                return await handle_session_answer(update, context)
            # waiting=False — ждём нажатия кнопки
            await update.message.reply_text("👆 Нажми одну из кнопок: 🔴 🟠 🟢 🔵")
            return
        if mode == "grammar_lesson":
            return await handle_grammar_lesson_answer(update, context)
        if mode == "reading":
            return await handle_reading_answer(update, context)
        if mode == "output":
            return await handle_output_answer(update, context)
        if mode == "weekly":
            return await handle_weekly_answer(update, context)
        if mode == "practice_cloze":
            r = safe_ds(f"Урок:\n{sessions[uid]['lesson']}\n\nОтветы:\n{text}\n\nПроверь. Макс 8 строк. НЕ используй _ * [ ] `.")
            await update.message.reply_text(r or "Ок.", reply_markup=main_kb())
            del sessions[uid]
            return
        if mode == "speaking_prompt":
            await update.message.reply_text("Нужно голосовое сообщение.")
            return

    add = detect_add(text)
    if add:
        word, trans, st = add
        _, ex = find_entry_anywhere(word)
        if ex:
            await update.message.reply_text(f"⚠️ Уже есть: {ex['word']}")
            return
        words.append(migrate_word({"word": f"{word} — {trans}", "status": st,
                                   "learned_at": datetime.now().isoformat() if st == "learning" else None}))
        save_json(WORDS_FILE, words)
        await update.message.reply_text(f"✅ {word} — {trans}", reply_markup=main_kb())
        return

    pv = detect_pv_add(text)
    if pv:
        word, trans = pv
        if get_word_entry("phrasal", word):
            await update.message.reply_text("Уже есть.")
            return
        phrasal_verbs.append(migrate_word({"word": f"{word} — {trans}", "status": "new"}))
        save_json(PHRASAL_FILE, phrasal_verbs)
        await update.message.reply_text(f"✅ ФГ: {word} — {trans}", reply_markup=main_kb())
        return

    track, e = find_entry_anywhere(text)
    if e:
        en, ru = e["word"].split(" — ")
        await update.message.reply_text(f"📖 {text}\n🇬🇧 {en}\n🇷🇺 {ru}")
        return

    if len(text.split('\n')) >= 2:
        r = safe_ds(f"Проверь переводы:\n{text}\nОцени, дай %. НЕ используй _ * [ ] `.") or "Не удалось."
        await update.message.reply_text(r, reply_markup=main_kb())
        return

    r = safe_ds(text, system="Ты репетитор английского для IT. Не используй _ * [ ] `.")
    await update.message.reply_text(r or "Ошибка.", reply_markup=main_kb())

# === РАССЫЛКА ===
async def send_daily_tasks():
    bot = Bot(token=os.environ.get('TELEGRAM_BOT_TOKEN'))
    users = load_json(USER_DATA_FILE, {})
    for uid, d in users.items():
        if not d.get("receives_daily", True):
            continue
        try:
            due = len(due_items("main")) + len(due_items("phrasal"))
            streak = d.get("streak", 0)
            await bot.send_message(
                chat_id=int(uid),
                text=f"🌞 Доброе утро!\n🔥 Streak: {streak}\n🔄 Due: {due}\n\nЖми «☀️ Дневная сессия»."
            )
        except Exception as e:
            logging.error(e)

# === КНОПКИ ===
async def button_handler(update, context):
    q = update.callback_query
    data = q.data

    if data == "menu_back":
        await q.answer()
        return await start(update, context)
    if data == "menu_more":
        await q.answer()
        return await q.edit_message_text("🔧 Дополнительно:", reply_markup=more_kb())
    if data == "menu_learn":
        await q.answer()
        return await learn_start(update, context, "main")
    if data == "menu_phrasal":
        await q.answer()
        return await q.edit_message_text("🔤 Фразовые глаголы:", reply_markup=phrasal_kb())
    if data == "menu_grammar":
        await q.answer()
        return await q.edit_message_text("📚 Грамматика:", reply_markup=grammar_kb())
    if data == "menu_reading":
        await q.answer()
        return await reading_start(update, context)
    if data == "menu_output":
        await q.answer()
        return await output_start(update, context)
    if data == "menu_progress":
        await q.answer()
        return await progress_menu(update, context)
    if data == "menu_practice":
        await q.answer()
        return await practice_cloze(update, context)
    if data == "menu_dialogue":
        await q.answer()
        return await dialogue_start(update, context)
    if data == "menu_leeches":
        await q.answer()
        return await leeches(update, context)
    if data == "menu_weekly":
        await q.answer()
        return await weekly_test(update, context)
    if data == "menu_speaking":
        await q.answer()
        return await speaking(update, context)
    if data == "menu_daily_progress":
        await q.answer()
        return await daily_progress(update, context)
    if data == "menu_plan":
        await q.answer()
        return await plan_menu(update, context)

    if data == "session_start":
        return await session_start(update, context)

    if data.startswith("srs_"):
        return await handle_srs_button(update, context)

    if data == "pv_daily":
        await q.answer()
        return await phrasal_daily(update, context)
    if data == "pv_learn":
        await q.answer()
        return await learn_start(update, context, "phrasal")
    if data == "pv_stats":
        await q.answer()
        due_p = len(due_items("phrasal"))
        return await q.edit_message_text(f"ФГ: {len(phrasal_verbs)}\nDue: {due_p}", reply_markup=phrasal_kb())

    if data == "grammar_new":
        await q.answer()
        return await grammar_new(update, context)
    if data == "grammar_due":
        await q.answer()
        return await grammar_due(update, context)
    if data == "grammar_progress":
        await q.answer()
        return await grammar_progress(update, context)
    if data == "grammar_next":
        return await grammar_next_topic(update, context)
    if data == "grammar_retry":
        return await grammar_retry(update, context)

    if data.startswith("mn_"):
        return await mnemonic(update, context)

    if data.startswith("plan_"):
        return await plan_set(update, context)

    if data == "menu_learning":
        await q.answer()
        lw = [w for w in words if w["status"] == "learning"]
        txt = "Слова на обучении:\n\n" + "\n".join(f"{i}. {w['word']}" for i, w in enumerate(lw, 1)) if lw else "Пусто"
        return await q.edit_message_text(txt, reply_markup=main_kb())

    await q.answer()

# === ЗАПУСК ===
async def post_init(application):
    scheduler.start()

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        logging.error("Нет TELEGRAM_BOT_TOKEN")
        return
    req = HTTPXRequest(connection_pool_size=8, read_timeout=30, write_timeout=30, connect_timeout=30)
    app = Application.builder().token(token).request(req).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("on", toggle_sub))
    app.add_handler(CommandHandler("off", toggle_sub))
    app.add_handler(CommandHandler("status", set_status_cmd))
    app.add_handler(CommandHandler("reset_learning", reset_learning))
    app.add_handler(CommandHandler("daily_words", lambda u, c: daily_words_compat(u, c, "main")))
    app.add_handler(CommandHandler("daily_phrasal", lambda u, c: daily_words_compat(u, c, "phrasal")))
    app.add_handler(CommandHandler("learn", lambda u, c: learn_start(u, c, "main")))
    app.add_handler(CommandHandler("learn_phrasal", lambda u, c: learn_start(u, c, "phrasal")))
    app.add_handler(CommandHandler("reading", lambda u, c: reading_start_compat(u, c)))
    app.add_handler(CommandHandler("grammar", lambda u, c: grammar_compat(u, c)))

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    scheduler.add_job(send_daily_tasks, CronTrigger(hour=9, minute=0), id='daily')
    print("✅ Бот запущен (C1 + SRS, финальная версия)!")
    app.run_polling()

async def daily_words_compat(update, context, track):
    q_msg = update.message
    uid = update.effective_user.id
    plan = get_plan(uid)
    taken = today_count(uid, "words" if track == "main" else "phrasal")
    limit = max(0, plan["words" if track == "main" else "phrasal"] - taken)
    new = new_items(track, limit)
    if not new:
        await q_msg.reply_text("Нет новых или лимит.")
        return
    for w in new:
        w["status"] = "learning"
        w["learned_at"] = datetime.now().isoformat()
    save_track(track)
    inc_today(uid, "words" if track == "main" else "phrasal", len(new))
    ex = gen_examples(new, 2)
    text = f"📚 Взято {len(new)}:\n\n" + "\n".join(f"{i}. {w['word']}" for i, w in enumerate(new, 1))
    if ex:
        text += f"\n\n📝 {ex}"
    for p in split_text(text, 4000):
        await q_msg.reply_text(p)

async def reading_start_compat(update, context):
    pool = [w for w in words if w["status"] in ("learning", "review")] or (words[:5] if words else [{"word": "algorithm — алгоритм"}])
    sel = random.sample(pool, min(5, len(pool)))
    text = gen_reading(sel)
    if not text:
        await update.message.reply_text("Не удалось.")
        return
    sessions[update.effective_user.id] = {"mode": "reading", "text": text}
    for p in split_text(f"📖 Чтение:\n\n{text}", 4000):
        await update.message.reply_text(p)
    await update.message.reply_text("Ответь на вопросы одним сообщением.")

async def grammar_compat(update, context):
    class FakeQ:
        def __init__(self, m): self.message = m; self.from_user = m.from_user
        async def edit_message_text(self, *a, **k):
            await self.message.reply_text(*a, **k)
        async def answer(self):
            pass
    class FakeU:
        def __init__(self, m): self.callback_query = FakeQ(m); self.message = m
    await grammar_new(FakeU(update.message), context)

if __name__ == "__main__":
    main()
