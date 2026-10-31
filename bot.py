# FunPay Meadow — Telegram-бот выдачи Premium-ключей
# Ключи хранятся в GitHub (keys.json) только в виде SHA-256 хешей — открытый файл не раскрывает сами ключи.
import os, json, base64, hashlib, secrets, time, threading
import requests
import telebot
from telebot import types

BOT_TOKEN   = os.environ["BOT_TOKEN"]                 # токен от @BotFather
ADMIN_IDS   = {int(x) for x in os.environ["ADMIN_IDS"].replace(" ", "").split(",") if x}  # ваш Telegram ID (можно несколько через запятую)
GH_TOKEN    = os.environ["GITHUB_TOKEN"]              # GitHub токен с правом записи в репозиторий
GH_REPO     = os.environ["GITHUB_REPO"]               # формат: ник/репозиторий
GH_BRANCH   = os.environ.get("GITHUB_BRANCH", "main")
GH_FILE     = os.environ.get("GITHUB_FILE", "keys.json")
PLANS       = [int(x) for x in os.environ.get("PLANS", "7,30,90,365").split(",")]

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
lock = threading.Lock()
API = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_FILE}"
HDR = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def sha(code): return hashlib.sha256(code.encode()).hexdigest()

def gh_load():
    r = requests.get(API, headers=HDR, params={"ref": GH_BRANCH}, timeout=20)
    if r.status_code == 404:
        return {"keys": []}, None
    r.raise_for_status()
    j = r.json()
    return json.loads(base64.b64decode(j["content"]).decode() or '{"keys":[]}'), j["sha"]

def gh_save(data, file_sha, msg):
    body = {"message": msg, "branch": GH_BRANCH,
            "content": base64.b64encode(json.dumps(data, ensure_ascii=False, indent=1).encode()).decode()}
    if file_sha: body["sha"] = file_sha
    r = requests.put(API, headers=HDR, json=body, timeout=20)
    r.raise_for_status()

def new_code():
    p = lambda: "".join(secrets.choice(ALPHABET) for _ in range(4))
    return f"MEAD-{p()}-{p()}-{p()}"

def generate(days, count=1):
    codes = [new_code() for _ in range(count)]
    with lock:
        data, fsha = gh_load()
        for c in codes:
            data["keys"].append({"h": sha(c), "days": days, "created": int(time.time())})
        gh_save(data, fsha, f"+{count} key(s) {days}d")
    return codes

def is_admin(m): return m.from_user.id in ADMIN_IDS

def menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(*[types.InlineKeyboardButton(f"🔑 {d} дн.", callback_data=f"gen:{d}:1") for d in PLANS])
    kb.add(*[types.InlineKeyboardButton(f"📦 5 × {d} дн.", callback_data=f"gen:{d}:5") for d in PLANS[:2]])
    kb.add(types.InlineKeyboardButton("📊 Статистика", callback_data="stat"),
           types.InlineKeyboardButton("🔎 Проверить ключ", callback_data="check"))
    return kb

@bot.message_handler(commands=["start", "menu"])
def start(m):
    if not is_admin(m):
        bot.reply_to(m, f"Ваш Telegram ID: <code>{m.from_user.id}</code>\nКлючи выдаёт администратор.")
        return
    bot.send_message(m.chat.id, "🌿 <b>FunPay Meadow · Premium</b>\nВыберите срок ключа:", reply_markup=menu())

@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    if c.from_user.id not in ADMIN_IDS:
        return bot.answer_callback_query(c.id, "Нет доступа")
    try:
        if c.data.startswith("gen:"):
            _, d, n = c.data.split(":")
            codes = generate(int(d), int(n))
            bot.answer_callback_query(c.id, "Готово")
            bot.send_message(c.message.chat.id,
                f"✅ Ключи на <b>{d} дн.</b> (появятся в расширении через ~5 мин):\n\n" +
                "\n".join(f"<code>{x}</code>" for x in codes), reply_markup=menu())
        elif c.data == "stat":
            data, _ = gh_load()
            by = {}
            for k in data["keys"]: by[k["days"]] = by.get(k["days"], 0) + 1
            txt = "\n".join(f"{d} дн. — {n} шт." for d, n in sorted(by.items())) or "пока пусто"
            bot.answer_callback_query(c.id)
            bot.send_message(c.message.chat.id, f"📊 Выдано ключей: {len(data['keys'])}\n{txt}")
        elif c.data == "check":
            bot.answer_callback_query(c.id)
            bot.send_message(c.message.chat.id, "Пришлите ключ командой:\n<code>/check MEAD-XXXX-XXXX-XXXX</code>")
    except Exception as e:
        bot.answer_callback_query(c.id, "Ошибка")
        bot.send_message(c.message.chat.id, f"❌ {e}")

@bot.message_handler(commands=["gen"])
def gen(m):
    if not is_admin(m): return
    a = m.text.split()[1:]
    try:
        days = int(a[0]); count = min(int(a[1]) if len(a) > 1 else 1, 50)
        codes = generate(days, count)
        bot.reply_to(m, "✅\n" + "\n".join(f"<code>{x}</code>" for x in codes))
    except Exception as e:
        bot.reply_to(m, f"Пример: /gen 30 5\n{e}")

@bot.message_handler(commands=["check"])
def check(m):
    if not is_admin(m): return
    a = m.text.split()
    if len(a) < 2: return bot.reply_to(m, "/check КЛЮЧ")
    data, _ = gh_load()
    h = sha(a[1].strip().upper())
    k = next((x for x in data["keys"] if x.get("h") == h), None)
    bot.reply_to(m, f"✅ Есть: {k['days']} дн." if k else "❌ Не найден")

@bot.message_handler(commands=["revoke"])
def revoke(m):
    if not is_admin(m): return
    a = m.text.split()
    if len(a) < 2: return bot.reply_to(m, "/revoke КЛЮЧ")
    h = sha(a[1].strip().upper())
    with lock:
        data, fsha = gh_load()
        n = len(data["keys"]); data["keys"] = [x for x in data["keys"] if x.get("h") != h]
        if len(data["keys"]) < n: gh_save(data, fsha, "revoke key")
    bot.reply_to(m, "🗑 Ключ отозван" if len(data["keys"]) < n else "Не найден")

if __name__ == "__main__":
    print("bot started")
    bot.infinity_polling(skip_pending=True)
