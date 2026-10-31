# FunPay Meadow — бот ключей v2: меню пользователя, баннеры, админ-панель, ключи на отдельные функции.
# Публичный keys.json (только хеши) -> читает расширение. Приватный db.json -> пользователи и сами ключи.
import os, io, json, base64, hashlib, secrets, time, threading, random, html, datetime
import requests, telebot
from telebot import types
from PIL import Image, ImageDraw, ImageFont

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ["ADMIN_IDS"].replace(" ", "").split(",") if x}
GH_TOKEN  = os.environ["GITHUB_TOKEN"]
PUB_REPO  = os.environ["GITHUB_REPO"]                 # публичный: keys.json (хеши)
DATA_REPO = os.environ["DATA_REPO"]                   # ПРИВАТНЫЙ: db.json (пользователи и ключи)
BRANCH    = os.environ.get("GITHUB_BRANCH", "main")
SUPPORT   = os.environ.get("SUPPORT", "")             # например @username или https://t.me/funmeadow
DAILY_BONUS = os.environ.get("DAILY_BONUS", "0") == "1"
HERE = os.path.dirname(os.path.abspath(__file__))
TZ = datetime.timezone(datetime.timedelta(hours=3))

# id -> (название, описание, цвет)
FEATURES = {
    "all":       ("Premium · ВСЕ функции", "Открывает абсолютно все функции ниже", (214, 168, 30)),
    "nick":      ("Ник и плашка", "Анимированные стили ника и бейдж рядом с ним", (168, 85, 247)),
    "quiet":     ("Тихие часы", "Уведомления не беспокоят в выбранное время", (59, 130, 246)),
    "theme":     ("Темы и расписание", "Premium-темы и автосмена день/ночь", (236, 72, 153)),
    "raise":     ("Умное поднятие", "Расписание, ночная пауза, приоритет лотов", (34, 197, 94)),
    "tpl":       ("Шаблоны ответов", "Быстрые ответы и автоответы по словам", (20, 184, 166)),
    "comp":      ("Мониторинг цен", "Следит за конкурентами, пишет если подрезали", (239, 68, 68)),
    "fx":        ("Курсор и эффекты", "След курсора, клики, живая шапка, хеллоуин", (249, 115, 22)),
    "analytics": ("Аналитика+", "Лучшие часы, сравнение недель, топ лотов", (14, 165, 233)),
    "backup":    ("Резервная копия", "Экспорт и импорт всех настроек файлом", (132, 204, 22)),
}
EMOJI = {"all": "👑", "nick": "🪪", "quiet": "🌙", "theme": "🎨", "raise": "🚀", "tpl": "💬", "comp": "🔍", "fx": "✨", "analytics": "📈", "backup": "💾"}
PLANS = [7, 30, 90, 365]
NEW_KEY_TITLES = ["🔥 НОВЫЙ КЛЮЧ !!!", "🎉 ТЕБЕ ПРИЛЕТЕЛ КЛЮЧ!", "💎 ДРОП! Ключ уже у тебя", "🚀 КЛЮЧ ДОСТАВЛЕН!", "⚡ ЕСТЬ НОВЫЙ КЛЮЧ!!!"]

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
lock = threading.RLock()
esc = html.escape
now = lambda: int(time.time())
fdate = lambda t: datetime.datetime.fromtimestamp(t, TZ).strftime("%d.%m.%Y")

# ---------------- GitHub-хранилище ----------------
class GH:
    def __init__(s, repo, path, default):
        s.url = f"https://api.github.com/repos/{repo}/contents/{path}"
        s.h = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
        s.default, s.sha, s.path = default, None, path
    def load(s):
        r = requests.get(s.url, headers=s.h, params={"ref": BRANCH}, timeout=25)
        if r.status_code == 404:
            s.sha = None; return json.loads(json.dumps(s.default))
        r.raise_for_status(); j = r.json(); s.sha = j["sha"]
        txt = base64.b64decode(j["content"]).decode() or "{}"
        d = json.loads(txt)
        for k, v in s.default.items(): d.setdefault(k, v)
        return d
    def save(s, data, msg="update"):
        body = {"message": msg, "branch": BRANCH,
                "content": base64.b64encode(json.dumps(data, ensure_ascii=False, indent=1).encode()).decode()}
        for attempt in range(3):
            if s.sha: body["sha"] = s.sha
            r = requests.put(s.url, headers=s.h, json=body, timeout=25)
            if r.status_code in (409, 422) and attempt < 2:   # sha устарел — берём свежий и пробуем снова
                g = requests.get(s.url, headers=s.h, params={"ref": BRANCH}, timeout=25)
                s.sha = g.json().get("sha") if g.status_code == 200 else None
                continue
            r.raise_for_status(); s.sha = r.json()["content"]["sha"]; return

pub_gh = GH(PUB_REPO, "keys.json", {"keys": []})
db_gh  = GH(DATA_REPO, "db.json", {"users": {}, "keys": {}})
pub = db = None

def sha(code): return hashlib.sha256(code.encode()).hexdigest()
def save_db(msg="db"): db_gh.save(db, msg)
def save_pub(msg="keys"): pub_gh.save(pub, msg)

# ---------------- ключи ----------------
ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
def mk_code():
    p = lambda: "".join(secrets.choice(ALPHA) for _ in range(4))
    return f"MEAD-{p()}-{p()}-{p()}"

def issue(fn, days, owner=None, by=None, count=1):
    assert fn in FEATURES
    codes = []
    with lock:
        for _ in range(count):
            c = mk_code()
            while c in db["keys"]: c = mk_code()
            db["keys"][c] = {"h": sha(c), "fn": fn, "days": days, "owner": owner, "by": by,
                             "issued": now(), "activated": None, "revoked": False, "warned": 0}
            entry = {"h": sha(c), "days": days}
            if fn != "all": entry["f"] = fn
            pub["keys"].append(entry)
            codes.append(c)
        save_db(f"+{count} {fn} {days}d"); save_pub(f"+{count} key(s)")
    return codes

def revoke(code):
    with lock:
        k = db["keys"].get(code)
        if not k: return False
        k["revoked"] = True
        pub["keys"] = [x for x in pub["keys"] if x.get("h") != k["h"]]
        save_db("revoke"); save_pub("revoke")
    return True

def key_end(k): return k["activated"] + k["days"] * 86400 if k.get("activated") else None

def status(k):
    if k.get("revoked"): return "⛔ отозван"
    if not k.get("activated"): return "🆕 ждёт активации"
    e = key_end(k)
    return f"🟢 активен до {fdate(e)}" if e > now() else "⌛ истёк"

def user_keys(uid): return [(c, k) for c, k in db["keys"].items() if k.get("owner") == uid]

def premium_state(uid):
    best = 0
    for c, k in user_keys(uid):
        if k["fn"] == "all" and not k.get("revoked") and k.get("activated"):
            e = key_end(k)
            if e > now(): best = max(best, e)
    return best

# ---------------- баннеры (Pillow) ----------------
def font(name, size):
    try:
        from fonts_data import DATA
        return ImageFont.truetype(io.BytesIO(base64.b64decode(DATA[name])), size)
    except Exception:
        for p in (os.path.join(HERE, name), os.path.join(HERE, "fonts", name)):
            if os.path.exists(p): return ImageFont.truetype(p, size)
        return ImageFont.load_default(size)
def grad(w, h, c1, c2):
    im = Image.new("RGB", (w, h)); d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / (h - 1); d.line([(0, y), (w, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    return im
def hills(im, col):
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov); w, h = im.size
    d.ellipse([-w * .2, h * .78, w * .55, h * 1.5], fill=col + (70,))
    d.ellipse([w * .3, h * .85, w * 1.2, h * 1.6], fill=col + (50,))
    d.ellipse([w * .7, -h * .5, w * 1.3, h * .35], fill=(255, 255, 255, 18))
    im.paste(Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB"))
def spaced(d, xy, text, f, fill, gap=0, anchor_center=None):
    widths = [d.textlength(ch, font=f) for ch in text]; total = sum(widths) + gap * (len(text) - 1)
    x, y = xy
    if anchor_center: x = anchor_center - total / 2
    for ch, wd in zip(text, widths):
        d.text((x, y), ch, font=f, fill=fill); x += wd + gap
def to_png(im):
    b = io.BytesIO(); im.save(b, "PNG"); b.seek(0); b.name = "banner.png"; return b

def key_banner(code, k):
    col = FEATURES[k["fn"]][2]; dark = tuple(int(c * .28) for c in col)
    W, H = 1000, 520; im = grad(W, H, tuple(int(c * .85) for c in col), dark); hills(im, col)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([40, 40, W - 40, H - 40], 28, outline=(255, 255, 255), width=3)
    d.text((80, 70), "FunPay Meadow", font=font("DejaVuSans-Bold.ttf", 34), fill=(255, 255, 255))
    d.text((80, 118), FEATURES[k["fn"]][0].upper(), font=font("DejaVuSans-Bold.ttf", 30), fill=(255, 255, 255))
    d.rounded_rectangle([80, 200, W - 80, 330], 22, fill=(0, 0, 0))
    spaced(d, (0, 232), code, font("DejaVuSansMono-Bold.ttf", 56), (255, 255, 255), gap=2, anchor_center=W / 2)
    d.text((80, 352), f"Срок: {k['days']} дн.   ·   Выдан: {fdate(k['issued'])}", font=font("DejaVuSans.ttf", 28), fill=(255, 255, 255))
    d.text((80, 396), status(k).replace("🆕", "").replace("🟢", "").replace("⌛", "").replace("⛔", "").strip(),
           font=font("DejaVuSans-Bold.ttf", 28), fill=(255, 255, 255))
    d.text((80, 436), "Расширение → Подписка → «Ключ активации»", font=font("DejaVuSans.ttf", 22), fill=(230, 230, 230))
    return to_png(im)

def welcome_banner(name, nkeys, prem_until):
    W, H = 1100, 560; c = (34, 160, 90)
    im = grad(W, H, (16, 70, 50), (8, 24, 28)); hills(im, c); d = ImageDraw.Draw(im)
    d.text((70, 70), "FunPay Meadow", font=font("DejaVuSans-Bold.ttf", 64), fill=(255, 255, 255))
    d.text((70, 150), "Ключи доступа к функциям", font=font("DejaVuSans.ttf", 34), fill=(190, 240, 210))
    d.rounded_rectangle([70, 240, W - 70, 400], 26, fill=(0, 0, 0))
    nm = name if len(name) <= 22 else name[:21] + "…"
    d.text((110, 262), f"Привет, {nm}!", font=font("DejaVuSans-Bold.ttf", 44), fill=(255, 255, 255))
    line = f"Ключей: {nkeys}   ·   " + (f"Premium до {fdate(prem_until)}" if prem_until else "Premium не активен")
    d.text((110, 330), line, font=font("DejaVuSans.ttf", 32), fill=(214, 168, 30) if prem_until else (200, 200, 200))
    d.text((70, 450), "Нажми «Мои ключи», чтобы увидеть все свои ключи", font=font("DejaVuSans.ttf", 28), fill=(220, 220, 220))
    return to_png(im)

# ---------------- меню ----------------
def is_admin(uid): return uid in ADMIN_IDS
def user_name(u): return (u.first_name or "") + ((" " + u.last_name) if u.last_name else "") or (u.username or "друг")

def register(u):
    with lock:
        rec = db["users"].get(str(u.id)); new = rec is None
        db["users"][str(u.id)] = {"name": user_name(u), "username": (u.username or "").lower(),
                                  "joined": rec["joined"] if rec else now(), "bonus": rec.get("bonus", "") if rec else ""}
        if new or rec.get("name") != user_name(u): save_db("user")

def main_menu(uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🔑 Мои ключи", callback_data="my"),
           types.InlineKeyboardButton("➕ Добавить ключ", callback_data="claim"))
    kb.add(types.InlineKeyboardButton("✨ Функции", callback_data="feat"),
           types.InlineKeyboardButton("📖 Как активировать", callback_data="how"))
    if DAILY_BONUS: kb.add(types.InlineKeyboardButton("🎲 Бонус дня", callback_data="bonus"))
    if SUPPORT: kb.add(types.InlineKeyboardButton("🆘 Поддержка", url=SUPPORT if SUPPORT.startswith("http") else f"https://t.me/{SUPPORT.lstrip('@')}"))
    if is_admin(uid): kb.add(types.InlineKeyboardButton("🛠 Админ-панель", callback_data="adm"))
    return kb

def send_welcome(chat_id, u):
    register(u)
    ks = user_keys(u.id)
    bot.send_photo(chat_id, welcome_banner(user_name(u), len(ks), premium_state(u.id)),
                   caption="🌿 <b>FunPay Meadow</b>\nЗдесь живут твои ключи. Выбери действие:", reply_markup=main_menu(u.id))

@bot.message_handler(commands=["start", "menu"])
def cmd_start(m): send_welcome(m.chat.id, m.from_user)

def my_keys_kb(uid):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for c, k in sorted(user_keys(uid), key=lambda x: -x[1]["issued"])[:25]:
        kb.add(types.InlineKeyboardButton(f"{EMOJI[k['fn']]} {FEATURES[k['fn']][0].split(' · ')[0]} · {k['days']}д · {status(k).split(' до')[0]}", callback_data=f"k:{c}"))
    kb.add(types.InlineKeyboardButton("⬅️ В меню", callback_data="home"))
    return kb

def key_caption(code, k):
    return (f"{EMOJI[k['fn']]} <b>{esc(FEATURES[k['fn']][0])}</b>\n<code>{code}</code>\n"
            f"Срок: {k['days']} дн. · {status(k)}\n\n<i>Нажми на ключ, чтобы скопировать.</i>")

def key_kb(code, k):
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not k.get("activated") and not k.get("revoked"):
        kb.add(types.InlineKeyboardButton("✅ Я активировал (запустить таймер)", callback_data=f"act:{code}"))
    kb.add(types.InlineKeyboardButton("⬅️ Мои ключи", callback_data="my"))
    return kb

def notify_new_key(uid, code):
    k = db["keys"][code]
    try:
        bot.send_photo(uid, key_banner(code, k), caption=f"<b>{random.choice(NEW_KEY_TITLES)}</b>\n\n" + key_caption(code, k), reply_markup=key_kb(code, k))
        return True
    except Exception: return False

# ---------------- админ-панель ----------------
def adm_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🎁 Выдать пользователю", callback_data="ag:u"),
           types.InlineKeyboardButton("📦 Свободные ключи", callback_data="ag:f"))
    kb.add(types.InlineKeyboardButton("👥 Пользователи", callback_data="ausers"),
           types.InlineKeyboardButton("📊 Статистика", callback_data="astat"))
    kb.add(types.InlineKeyboardButton("🔎 Найти ключ", callback_data="afind"),
           types.InlineKeyboardButton("🗑 Отозвать ключ", callback_data="arev"))
    kb.add(types.InlineKeyboardButton("📢 Рассылка", callback_data="acast"),
           types.InlineKeyboardButton("⬅️ В меню", callback_data="home"))
    return kb

def fn_kb(prefix):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(*[types.InlineKeyboardButton(f"{EMOJI[f]} {FEATURES[f][0].split(' · ')[0]}", callback_data=f"{prefix}:{f}") for f in FEATURES])
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="adm"))
    return kb

def days_kb(prefix):
    kb = types.InlineKeyboardMarkup(row_width=4)
    kb.add(*[types.InlineKeyboardButton(f"{d} дн.", callback_data=f"{prefix}:{d}") for d in PLANS])
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="adm"))
    return kb

def find_user(text):
    t = text.strip().lstrip("@").lower()
    if t.isdigit(): return int(t) if t in db["users"] or True else None
    for uid, u in db["users"].items():
        if u.get("username") == t: return int(uid)
    return None

def stats_text():
    ks = db["keys"].values(); by = {}
    for k in ks: by[k["fn"]] = by.get(k["fn"], 0) + 1
    lines = [f"{EMOJI[f]} {FEATURES[f][0].split(' · ')[0]}: {n}" for f, n in by.items()]
    act = sum(1 for k in ks if k.get("activated") and not k.get("revoked") and key_end(k) > now())
    return (f"📊 <b>Статистика</b>\nПользователей: {len(db['users'])}\nКлючей выдано: {len(db['keys'])}\n"
            f"Привязано к людям: {sum(1 for k in ks if k.get('owner'))} · свободных: {sum(1 for k in ks if not k.get('owner'))}\n"
            f"Активны по отметкам: {act}\nОтозвано: {sum(1 for k in ks if k.get('revoked'))}\n\n" + ("\n".join(lines) or "Ключей пока нет"))

def ask(chat_id, text, handler, *args):
    msg = bot.send_message(chat_id, text); bot.register_next_step_handler(msg, handler, *args)

def step_give(m, fn, days):
    if not is_admin(m.from_user.id): return
    uid = find_user(m.text or "")
    if uid is None or str(uid) not in db["users"]:
        return bot.send_message(m.chat.id, "❌ Не нашёл такого пользователя. Он должен нажать /start у бота. Либо создайте «📦 Свободный ключ» и отправьте вручную.", reply_markup=adm_kb())
    code = issue(fn, days, owner=uid, by=m.from_user.id)[0]
    sent = notify_new_key(uid, code)
    bot.send_message(m.chat.id, f"✅ Ключ выдан: <code>{code}</code>\n" + ("Уведомление отправлено." if sent else "⚠️ Не удалось отправить (пользователь заблокировал бота)."), reply_markup=adm_kb())

def step_find(m):
    if not is_admin(m.from_user.id): return
    c = (m.text or "").strip().upper(); k = db["keys"].get(c)
    if not k: return bot.send_message(m.chat.id, "❌ Не найден", reply_markup=adm_kb())
    o = db["users"].get(str(k.get("owner")), {})
    bot.send_message(m.chat.id, key_caption(c, k) + f"\nВладелец: {esc(o.get('name', 'нет'))} {k.get('owner') or ''}", reply_markup=adm_kb())

def step_revoke(m):
    if not is_admin(m.from_user.id): return
    c = (m.text or "").strip().upper(); k = db["keys"].get(c)
    ok = revoke(c)
    if ok and k.get("owner"):
        try: bot.send_message(k["owner"], f"⛔ Ключ <code>{c}</code> отозван администратором.")
        except Exception: pass
    bot.send_message(m.chat.id, "🗑 Ключ отозван" if ok else "❌ Не найден", reply_markup=adm_kb())

def step_cast(m):
    if not is_admin(m.from_user.id): return
    kb = types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("✅ Отправить всем", callback_data="acast_ok"), types.InlineKeyboardButton("❌ Отмена", callback_data="adm"))
    cast_buf[m.from_user.id] = m.html_text or m.text or ""
    bot.send_message(m.chat.id, f"Предпросмотр рассылки ({len(db['users'])} получателей):\n\n{cast_buf[m.from_user.id]}", reply_markup=kb)
cast_buf = {}

def step_claim(m):
    c = (m.text or "").strip().upper().replace(" ", ""); k = db["keys"].get(c)
    if not k or k.get("revoked"): return bot.send_message(m.chat.id, "❌ Такой ключ не найден.", reply_markup=main_menu(m.from_user.id))
    with lock:
        if k.get("owner") and k["owner"] != m.from_user.id:
            return bot.send_message(m.chat.id, "❌ Этот ключ уже привязан к другому пользователю.", reply_markup=main_menu(m.from_user.id))
        k["owner"] = m.from_user.id; register(m.from_user); save_db("claim")
    bot.send_photo(m.chat.id, key_banner(c, k), caption="✅ Ключ добавлен в ваш список!\n\n" + key_caption(c, k), reply_markup=key_kb(c, k))

# ---------------- callbacks ----------------
@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    uid, cid, d = c.from_user.id, c.message.chat.id, c.data
    try:
        bot.answer_callback_query(c.id)
        if d == "home": return send_welcome(cid, c.from_user)
        if d == "my":
            ks = user_keys(uid)
            return bot.send_message(cid, "🔑 <b>Твои ключи</b>" if ks else "У тебя пока нет ключей. Купи ключ или нажми «➕ Добавить ключ».", reply_markup=my_keys_kb(uid))
        if d.startswith("k:"):
            code = d[2:]; k = db["keys"].get(code)
            if not k or k.get("owner") != uid: return bot.send_message(cid, "❌ Ключ не найден")
            return bot.send_photo(cid, key_banner(code, k), caption=key_caption(code, k), reply_markup=key_kb(code, k))
        if d.startswith("act:"):
            code = d[4:]; k = db["keys"].get(code)
            if k and k.get("owner") == uid and not k.get("activated"):
                with lock: k["activated"] = now(); save_db("act")
                return bot.send_message(cid, f"🟢 Таймер запущен. Ключ активен до <b>{fdate(key_end(k))}</b>. Напомню за 3 дня до конца!", reply_markup=my_keys_kb(uid))
            return
        if d == "claim": return ask(cid, "Пришли ключ вида MEAD-XXXX-XXXX-XXXX:", step_claim)
        if d == "feat":
            txt = "✨ <b>Функции FunPay Meadow</b>\n\n" + "\n".join(f"{EMOJI[f]} <b>{esc(v[0])}</b> — {esc(v[1])}" for f, v in FEATURES.items() if f != "all")
            return bot.send_message(cid, txt + "\n\n👑 <b>Premium</b> открывает ВСЕ функции сразу; обычный ключ — одну.")
        if d == "how":
            return bot.send_message(cid, "📖 <b>Как активировать</b>\n1. Откройте FunPay с расширением Meadow.\n2. Раздел <b>Подписка</b> → поле «Ключ активации».\n3. Вставьте ключ и нажмите «Активировать».\n4. Вернитесь сюда и нажмите «✅ Я активировал» у ключа — бот напомнит о конце срока.")
        if d == "bonus" and DAILY_BONUS:
            today = datetime.datetime.now(TZ).strftime("%Y-%m-%d"); u = db["users"].get(str(uid))
            if u and u.get("bonus") == today: return bot.send_message(cid, "🎲 Бонус сегодня уже получен. Приходи завтра!")
            with lock: u["bonus"] = today; save_db("bonus")
            fn = random.choice([f for f in FEATURES if f != "all"])
            return notify_new_key(uid, issue(fn, 1, owner=uid, by=0)[0])
        # ---- админ ----
        if not is_admin(uid): return
        if d == "adm": return bot.send_message(cid, "🛠 <b>Админ-панель</b>", reply_markup=adm_kb())
        if d.startswith("ag:"): return bot.send_message(cid, "Какая функция?", reply_markup=fn_kb("af:" + d[3:]))
        if d.startswith("af:"):
            _, mode, fn = d.split(":"); return bot.send_message(cid, f"{EMOJI[fn]} {FEATURES[fn][0]}\nНа сколько дней?", reply_markup=days_kb(f"ad:{mode}:{fn}"))
        if d.startswith("ad:"):
            _, mode, fn, days = d.split(":"); days = int(days)
            if mode == "u": return ask(cid, "Пришли ID или @username получателя (он должен был нажать /start):", step_give, fn, days)
            kb = types.InlineKeyboardMarkup(row_width=4)
            kb.add(*[types.InlineKeyboardButton(f"×{n}", callback_data=f"an:{fn}:{days}:{n}") for n in (1, 5, 10, 20)])
            return bot.send_message(cid, "Сколько ключей создать?", reply_markup=kb)
        if d.startswith("an:"):
            _, fn, days, n = d.split(":"); codes = issue(fn, int(days), by=uid, count=int(n))
            return bot.send_message(cid, f"📦 {EMOJI[fn]} {FEATURES[fn][0]} · {days} дн.\n\n" + "\n".join(f"<code>{x}</code>" for x in codes) + "\n\nПокупатель может добавить ключ кнопкой «➕ Добавить ключ».", reply_markup=adm_kb())
        if d == "ausers":
            us = sorted(db["users"].items(), key=lambda x: -x[1]["joined"])[:15]
            return bot.send_message(cid, f"👥 Всего: {len(db['users'])}\n\n" + "\n".join(f"{esc(u['name'])} {('@' + u['username']) if u.get('username') else ''} <code>{i}</code>" for i, u in us), reply_markup=adm_kb())
        if d == "astat": return bot.send_message(cid, stats_text(), reply_markup=adm_kb())
        if d == "afind": return ask(cid, "Пришли ключ:", step_find)
        if d == "arev": return ask(cid, "Пришли ключ для отзыва:", step_revoke)
        if d == "acast": return ask(cid, "Пришли текст рассылки (можно с форматированием):", step_cast)
        if d == "acast_ok":
            text = cast_buf.pop(uid, "")
            if not text: return
            ok = bad = 0
            for i in list(db["users"]):
                try: bot.send_message(int(i), "📢 " + text); ok += 1
                except Exception: bad += 1
                time.sleep(0.05)
            return bot.send_message(cid, f"Готово: доставлено {ok}, не доставлено {bad}", reply_markup=adm_kb())
    except Exception as e:
        try: bot.send_message(cid, f"❌ Ошибка: {esc(str(e))[:300]}")
        except Exception: pass

@bot.message_handler(commands=["admin"])
def cmd_admin(m):
    if is_admin(m.from_user.id): bot.send_message(m.chat.id, "🛠 <b>Админ-панель</b>", reply_markup=adm_kb())

# ---------------- напоминания ----------------
def reminders():
    while True:
        try:
            with lock:
                changed = False
                for c, k in db["keys"].items():
                    if k.get("revoked") or not k.get("activated") or not k.get("owner"): continue
                    left = key_end(k) - now()
                    if left <= 0 and k["warned"] < 2:
                        k["warned"] = 2; changed = True
                        try: bot.send_message(k["owner"], f"⌛ Срок ключа <code>{c}</code> ({esc(FEATURES[k['fn']][0])}) закончился. Нужен новый? Напиши администратору.")
                        except Exception: pass
                    elif 0 < left <= 3 * 86400 and k["warned"] < 1:
                        k["warned"] = 1; changed = True
                        try: bot.send_message(k["owner"], f"⏰ Ключ <code>{c}</code> ({esc(FEATURES[k['fn']][0])}) закончится через {max(1, left // 86400)} дн.")
                        except Exception: pass
                if changed: save_db("reminders")
        except Exception as e: print("reminders error:", e)
        time.sleep(1800)

def _health():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class H(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        def log_message(self, *a): pass
    HTTPServer(("0.0.0.0", int(os.environ["PORT"])), H).serve_forever()

def init():
    global pub, db
    pub = pub_gh.load(); db = db_gh.load()

if __name__ == "__main__":
    init()
    if os.environ.get("PORT"): threading.Thread(target=_health, daemon=True).start()
    threading.Thread(target=reminders, daemon=True).start()
    print("bot started")
    bot.infinity_polling(skip_pending=True)
