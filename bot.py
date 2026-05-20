import logging, asyncio, aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

import config
import database as db

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

CHANNEL_ID = "@v2greenorg"  # کانال اجباری

PLAN_LABELS = {
    "1gb": "اشتراک ۱ گیگ", "2gb": "اشتراک ۲ گیگ",
    "50mb": "تست ۵۰ مگ", "100mb": "تست ۱۰۰ مگ", "referral": "رفرال",
}

# ── Helpers ───────────────────────────────────────────────

def is_admin(uid): return uid in config.ADMIN_IDS or uid in db.get_admin_ids()
def all_admins(): return list(set(config.ADMIN_IDS + db.get_admin_ids()))
def fmt(p): return f"{p:,} تومان"
def flag(key, default="1"): return db.get_setting(key, default) != "0"

def card(): return db.get_setting("card_number") or config.CARD_NUMBER
def cardholder(): return db.get_setting("card_holder") or config.CARD_HOLDER
def ton_wallet(): return db.get_setting("ton_wallet_address") or getattr(config, "TON_WALLET_ADDRESS", "")
def price(key): return int(db.get_setting(f"price_{key}") or 0) or \
    (config.PLANS.get(key) or config.TEST_PLANS.get(key) or {}).get("price", 0)

async def get_ton_price_toman() -> float:
    val = db.get_setting("ton_price_toman")
    try:
        return float(val) if val else 0
    except Exception:
        return 0

async def toman_to_ton(toman: int) -> float:
    rate = await get_ton_price_toman()
    if rate <= 0:
        return 0
    return round(toman / rate, 4)

def uinfo(u):
    un = f"@{u['username']}" if u.get("username") else "ندارد"
    return f"👤 {u['full_name']}\n🔗 {un}\n🆔 `{u['user_id']}`"

async def is_member(bot, user_id):
    try:
        m = await bot.get_chat_member(CHANNEL_ID, user_id)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False

def main_kb(uid=None):
    rows = [
        [KeyboardButton("🛒 خرید اشتراک"), KeyboardButton("🧪 اکانت تست")],
        [KeyboardButton("👥 زیرمجموعه‌گیری"), KeyboardButton("🎧 پشتیبانی")],
        [KeyboardButton("👤 حساب من"), KeyboardButton("💳 افزایش موجودی")],
    ]
    if uid and is_admin(uid):
        rows.append([KeyboardButton("🔧 پنل ادمین")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def main_inline_kb(uid=None):
    rows = [
        [
            InlineKeyboardButton("✅ خرید کانفیگ جدید", callback_data="menu_buy"),
            InlineKeyboardButton("💳 کیف پول", callback_data="menu_wallet"),
        ],
        [
            InlineKeyboardButton("👑 پشتیبانی", callback_data="menu_support"),
            InlineKeyboardButton("🎁 کانفیگ رایگان", callback_data="menu_free"),
        ],
    ]
    if uid and is_admin(uid):
        rows.append([InlineKeyboardButton("🔧 پنل ادمین", callback_data="menu_admin")])
    return InlineKeyboardMarkup(rows)

def join_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/v2greenorg")],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")],
    ])

def admin_kb():
    s_sales = "🟢 فروش باز" if flag("sales_open") else "🔴 فروش بسته"
    s_card  = "🟢 کارت باز" if flag("card_open") else "🔴 کارت بسته"
    s_topup = "🟢 شارژ باز" if flag("topup_open") else "🔴 شارژ بسته"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 کاربران", callback_data="a_users"),
         InlineKeyboardButton("💰 پرداخت‌های در انتظار", callback_data="a_pays")],
        [InlineKeyboardButton("📦 مدیریت کانفیگ‌ها", callback_data="a_configs")],
        [InlineKeyboardButton("💲 قیمت‌ها", callback_data="a_prices"),
         InlineKeyboardButton("💳 شماره کارت", callback_data="a_card")],
        [InlineKeyboardButton("💎 آدرس ولت TON", callback_data="a_wallet"),
         InlineKeyboardButton("💲 قیمت TON", callback_data="a_ton_price")],
        [InlineKeyboardButton("👤 مدیریت ادمین‌ها", callback_data="a_admins")],
        [InlineKeyboardButton("💰 موجودی کاربر", callback_data="a_balance"),
         InlineKeyboardButton("📢 پیام همگانی", callback_data="a_broadcast")],
        [InlineKeyboardButton(f"{s_sales} ← تغییر", callback_data="a_toggle_sales")],
        [InlineKeyboardButton(f"{s_card} ← تغییر", callback_data="a_toggle_card"),
         InlineKeyboardButton(f"{s_topup} ← تغییر", callback_data="a_toggle_topup")],
    ])

def back_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")]])

# ── State ─────────────────────────────────────────────────

def gs(uid): return db.load_state(uid)
def ss(uid, state): db.save_state(uid, state)
def cs(uid): db.clear_state(uid)

# ── Payment timeout ───────────────────────────────────────

async def pay_timeout(bot, pay_id, user_id, chat_id, secs):
    await asyncio.sleep(secs)
    pay = db.get_payment(pay_id)
    if pay and pay["status"] == "pending":
        db.cancel_payment(pay_id)
        cs(user_id)
        try:
            await bot.send_message(chat_id, "⏰ زمان پرداخت تمام شد و سفارش لغو شد.", reply_markup=main_kb(user_id))
        except Exception: pass

# ── Channel check decorator ───────────────────────────────

async def require_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if is_admin(uid): return True
    if not await is_member(context.bot, uid):
        text = "⛔️ برای استفاده از ربات باید عضو کانال ما باشید:"
        kb = join_kb()
        if update.message:
            await update.message.reply_text(text, reply_markup=kb)
        elif update.callback_query:
            await update.callback_query.answer("ابتدا عضو کانال شوید", show_alert=True)
        return False
    return True

# ── /start ────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    cs(user.id)

    ref = None
    if context.args:
        ru = db.get_user_by_referral(context.args[0])
        if ru and ru["user_id"] != user.id:
            ref = ru["user_id"]

    db_user = db.get_or_create_user(user.id, user.username or "", user.full_name or "", ref)

    # اطلاع دعوت‌کننده
    if ref and db_user["_is_new"]:
        ref_owner = db.get_user(ref)
        if ref_owner:
            try:
                await context.bot.send_message(
                    ref, f"🎉 دعوت شما موفق بود!\n"
                         f"👤 {db_user['full_name']} عضو شد.\n"
                         f"📊 مجموع دعوت‌های شما: {ref_owner['referral_count'] + 1}"
                )
            except Exception: pass

            # بررسی آستانه رفرال
            updated = db.get_user(ref)
            if updated and updated["referral_count"] >= config.REFERRAL_THRESHOLD and not updated["referral_rewarded"]:
                db.mark_referral_rewarded(ref)
                cfg = db.assign_config("referral", ref)
                if cfg:
                    try:
                        await context.bot.send_message(ref,
                            f"🎊 تبریک! به {config.REFERRAL_THRESHOLD} دعوت رسیدید!\n\n"
                            f"🎁 کانفیگ رایگان شما:\n`{cfg}`", parse_mode="Markdown")
                    except Exception: pass
                else:
                    for aid in all_admins():
                        try:
                            await context.bot.send_message(aid,
                                f"🎉 کاربر به {config.REFERRAL_THRESHOLD} دعوت رسید:\n{uinfo(updated)}\n\n"
                                f"⚠️ کانفیگ رفرال موجود نیست!")
                        except Exception: pass
                    try:
                        await context.bot.send_message(ref,
                            "🎊 تبریک! دعوت شما موفق بود.\nکانفیگ شما طی ساعات آینده ارسال می‌شود.")
                    except Exception: pass

    if not await is_member(context.bot, user.id) and not is_admin(user.id):
        await update.message.reply_text(
            f"سلام {user.first_name} عزیز! 👋\n\n"
            "برای استفاده از ربات ابتدا باید عضو کانال ما شوید:",
            reply_markup=join_kb()
        )
        return

    await update.message.reply_text(
        f"🔄 به فروشگاه ConfigSatan خوش آمدید!\n\n"
        f"🛡 ارائه انواع سرویس‌های VPN با کیفیت عالی\n"
        f"✅ تضمین امنیت ارتباطات شما\n"
        f"📞 پشتیبانی حرفه‌ای ۲۴ ساعته\n\n"
        f"از منوی زیر بخش مورد نظر خود را انتخاب کنید.",
        reply_markup=main_inline_kb(user.id)
    )

# ── Message router ────────────────────────────────────────

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text or ""
    state = gs(uid)
    w = state.get("w")

    # وضعیت‌های انتظار
    if w:
        if w == "receipt":       await recv_receipt(update, context); return
        if w == "topup_receipt": await recv_topup_receipt(update, context); return
        if w == "support":       await recv_support(update, context); return
        if w == "topup_amount":  await recv_topup_amount(update, context); return
        if w == "a_bal_uid":     await a_recv_bal_uid(update, context); return
        if w == "a_bal_amt":     await a_recv_bal_amt(update, context); return
        if w == "a_price":       await a_recv_price(update, context); return
        if w == "a_configs":     await a_recv_configs(update, context); return
        if w == "a_broadcast":   await a_recv_broadcast(update, context); return
        if w == "a_add_admin":   await a_recv_add_admin(update, context); return
        if w == "a_del_admin":   await a_recv_del_admin(update, context); return
        if w == "a_card":        await a_recv_card(update, context); return
        if w == "a_wallet":      await a_recv_wallet(update, context); return
        if w == "a_ton_price":   await a_recv_ton_price(update, context); return
        if w == "a_send_cfg":    await a_recv_send_cfg(update, context); return

    # چک عضویت
    if not await require_member(update, context): return

    if text == "🛒 خرید اشتراک":  await show_plans(update, context, config.PLANS, "sub")
    elif text == "🧪 اکانت تست":  await show_plans(update, context, config.TEST_PLANS, "test")
    elif text == "👥 زیرمجموعه‌گیری": await show_referral(update, context)
    elif text == "🎧 پشتیبانی":   await start_support(update, context)
    elif text == "👤 حساب من":    await show_account(update, context)
    elif text == "💳 افزایش موجودی": await start_topup(update, context)
    elif text == "🔧 پنل ادمین" and is_admin(uid): await show_admin(update, context)

async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = gs(uid)
    w = state.get("w")
    logger.info(f"on_photo: uid={uid}, state={state}, w={w}")
    if w == "receipt":
        await recv_receipt(update, context)
    elif w == "topup_receipt":
        await recv_topup_receipt(update, context)
    else:
        # اگه state نداشت ولی عکس فرستاد، شاید state از دست رفته
        logger.warning(f"on_photo: no matching state for uid={uid}, state={state}")

# ── Callbacks ─────────────────────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    uid = q.from_user.id

    if d == "check_join":
        if await is_member(context.bot, uid):
            await q.edit_message_text("✅ عضویت تایید شد!", reply_markup=None)
            await context.bot.send_message(
                uid,
                "🔄 به فروشگاه ConfigSatan خوش آمدید!\n\n"
                "🛡 ارائه انواع سرویس‌های VPN با کیفیت عالی\n"
                "✅ تضمین امنیت ارتباطات شما\n"
                "📞 پشتیبانی حرفه‌ای ۲۴ ساعته\n\n"
                "از منوی زیر بخش مورد نظر خود را انتخاب کنید.",
                reply_markup=main_inline_kb(uid)
            )
        else:
            await q.answer("هنوز عضو نشدید! ابتدا عضو کانال شوید.", show_alert=True)
        return

    # دکمه‌های منوی اصلی inline
    if d == "menu_buy":
        if not await require_member(update, context): return
        await q.answer()
        class FakeUpdate:
            message = q.message
            effective_user = q.from_user
            callback_query = q
        await show_plans(FakeUpdate(), context, config.PLANS, "sub")
        return
    elif d == "menu_wallet":
        if not await require_member(update, context): return
        await q.answer()
        class FakeUpdate:
            message = q.message
            effective_user = q.from_user
            callback_query = q
        await start_topup(FakeUpdate(), context)
        return
    elif d == "menu_support":
        if not await require_member(update, context): return
        await q.answer()
        class FakeUpdate:
            message = q.message
            effective_user = q.from_user
            callback_query = q
        await start_support(FakeUpdate(), context)
        return
    elif d == "menu_free":
        if not await require_member(update, context): return
        await q.answer()
        class FakeUpdate:
            message = q.message
            effective_user = q.from_user
            callback_query = q
        await show_referral(FakeUpdate(), context)
        return
    elif d == "menu_admin":
        if not is_admin(uid): return
        await q.answer()
        class FakeUpdate:
            message = q.message
            effective_user = q.from_user
            callback_query = q
        await show_admin(FakeUpdate(), context)
        return

    # چک عضویت برای کاربر عادی
    if not is_admin(uid) and not d.startswith("a_"):
        if not await is_member(context.bot, uid):
            await q.answer("ابتدا عضو کانال شوید", show_alert=True)
            return

    # انتخاب پلن
    if d.startswith("plan_") or d.startswith("test_"):
        parts = d.split("_", 1); ptype = parts[0]; key = parts[1]
        plans = config.PLANS if ptype == "plan" else config.TEST_PLANS
        plan = plans.get(key)
        if plan:
            if not flag("sales_open"):
                await q.edit_message_text("🔴 فروش در حال حاضر بسته است.")
                return
            p = dict(plan); p["price"] = price(key)
            await show_invoice(q, uid, p, key, "sub" if ptype == "plan" else "test")

    elif d.startswith("pay_card_"):
        key = d[9:]
        await do_card_payment(q, uid, key, context)

    elif d.startswith("pay_ton_"):
        key = d[8:]
        await do_ton_payment(q, uid, key, context)

    elif d.startswith("pay_wallet_"):
        key = d[11:]
        await do_wallet_payment(q, uid, key, context)

    elif d == "cancel":
        cs(uid)
        await q.edit_message_text("❌ عملیات لغو شد.")

    elif d == "topup_card":
        if not flag("topup_open"):
            await q.edit_message_text("🔴 افزایش موجودی غیرفعال است."); return
        ss(uid, {"w": "topup_amount", "method": "card"})
        await q.edit_message_text(
            "💳 مبلغ شارژ را وارد کنید (۵۰,۰۰۰ تا ۵,۰۰۰,۰۰۰ تومان):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel")]]))

    elif d == "topup_ton":
        if not flag("topup_open"):
            await q.edit_message_text("🔴 افزایش موجودی غیرفعال است."); return
        wallet = ton_wallet()
        if not wallet:
            await q.edit_message_text("⚠️ آدرس ولت TON تنظیم نشده است."); return
        ton_rate = await get_ton_price_toman()
        if ton_rate <= 0:
            await q.edit_message_text("⚠️ قیمت TON توسط ادمین تنظیم نشده است."); return
        ss(uid, {"w": "topup_amount", "method": "ton"})
        await q.edit_message_text(
            f"💎 مبلغ شارژ را به تومان وارد کنید:\n(معادل TON محاسبه می‌شود)\n\n💲 قیمت هر TON: {fmt(int(ton_rate))}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel")]])
        )

    # ادمین تایید/رد
    elif d.startswith("ac_"):  # admin confirm
        pay_id = int(d[3:])
        await admin_confirm(q, pay_id, context)

    elif d.startswith("ar_"):  # admin reject
        pay_id = int(d[3:])
        await admin_reject(q, pay_id, context)

    elif d.startswith("am_"):  # admin message user
        target = int(d[3:])
        if not is_admin(uid): return
        ss(uid, {"w": "a_send_cfg", "target": target, "mode": "msg"})
        await q.edit_message_text("✍️ پیام خود را بنویسید:")

    elif d.startswith("asc_"):  # admin send config manually
        pay_id = int(d[4:])
        if not is_admin(uid): return
        pay = db.get_payment(pay_id)
        if not pay: return
        ss(uid, {"w": "a_send_cfg", "target": pay["user_id"], "pay_id": pay_id, "mode": "cfg"})
        await q.edit_message_text(
            f"📦 کانفیگ را برای ارسال به کاربر `{pay['user_id']}` وارد کنید:",
            parse_mode="Markdown"
        )

    # پنل ادمین
    elif d == "a_back":
        await q.edit_message_text("🔧 *پنل مدیریت*", parse_mode="Markdown", reply_markup=admin_kb())
    elif d == "a_users":    await a_show_users(q)
    elif d == "a_pays":     await a_show_pays(q)
    elif d == "a_configs":  await a_show_configs(q, uid)
    elif d == "a_prices":   await a_show_prices(q, uid)
    elif d == "a_card":
        if not is_admin(uid): return
        ss(uid, {"w": "a_card", "step": "number"})
        await q.edit_message_text(f"💳 شماره کارت فعلی: `{card()}`\n\nشماره جدید را وارد کنید:", parse_mode="Markdown")
    elif d == "a_wallet":
        if not is_admin(uid): return
        cur = ton_wallet()
        ss(uid, {"w": "a_wallet"})
        await q.edit_message_text(
            f"💎 *آدرس ولت TON*\n\nآدرس فعلی:\n`{cur if cur else 'تنظیم نشده'}`\n\nآدرس جدید ولت را وارد کنید:",
            parse_mode="Markdown", reply_markup=back_kb()
        )
    elif d == "a_ton_price":
        if not is_admin(uid): return
        cur = db.get_setting("ton_price_toman") or "تنظیم نشده"
        ss(uid, {"w": "a_ton_price"})
        await q.edit_message_text(
            f"💲 *قیمت هر TON به تومان*\n\nقیمت فعلی: `{cur}`\n\nقیمت جدید را وارد کنید (تومان):\nمثال: `428000`",
            parse_mode="Markdown", reply_markup=back_kb()
        )
    elif d == "a_admins":   await a_show_admins(q, uid)
    elif d == "a_add_admin":
        if not is_admin(uid): return
        ss(uid, {"w": "a_add_admin"})
        await q.edit_message_text("آیدی عددی کاربر جدید را وارد کنید:")
    elif d == "a_del_admin":
        if not is_admin(uid): return
        ss(uid, {"w": "a_del_admin"})
        await q.edit_message_text("آیدی عددی ادمین را برای حذف وارد کنید:")
    elif d == "a_balance":
        if not is_admin(uid): return
        ss(uid, {"w": "a_bal_uid"})
        await q.edit_message_text("آیدی عددی کاربر را وارد کنید:")
    elif d == "a_broadcast":
        if not is_admin(uid): return
        ss(uid, {"w": "a_broadcast"})
        await q.edit_message_text("📢 متن پیام همگانی را بنویسید:")
    elif d == "a_toggle_sales":
        if not is_admin(uid): return
        v = flag("sales_open"); db.set_setting("sales_open", "0" if v else "1")
        await q.edit_message_text(f"✅ فروش {'بسته' if v else 'باز'} شد.", reply_markup=back_kb())
    elif d == "a_toggle_card":
        if not is_admin(uid): return
        v = flag("card_open"); db.set_setting("card_open", "0" if v else "1")
        await q.edit_message_text(f"✅ پرداخت کارت {'بسته' if v else 'باز'} شد.", reply_markup=back_kb())
    elif d == "a_toggle_topup":
        if not is_admin(uid): return
        v = flag("topup_open"); db.set_setting("topup_open", "0" if v else "1")
        await q.edit_message_text(f"✅ افزایش موجودی {'بسته' if v else 'باز'} شد.", reply_markup=back_kb())
    elif d.startswith("a_addcfg_"):
        plan_key = d[9:]
        if not is_admin(uid): return
        ss(uid, {"w": "a_configs", "plan_key": plan_key})
        await q.edit_message_text(
            f"📦 *افزودن کانفیگ — {PLAN_LABELS.get(plan_key, plan_key)}*\n\nهر کانفیگ را در یک خط جداگانه بنویسید:",
            parse_mode="Markdown"
        )
    elif d.startswith("a_setprice_"):
        key = d[11:]
        if not is_admin(uid): return
        ss(uid, {"w": "a_price", "key": key})
        cur = price(key)
        await q.edit_message_text(f"قیمت فعلی «{PLAN_LABELS.get(key,key)}»: {fmt(cur)}\n\nقیمت جدید (تومان):")

# ── Plans / Invoice ───────────────────────────────────────

async def show_plans(update, context, plans, ptype):
    if not flag("sales_open"):
        await update.message.reply_text("🔴 فروش در حال حاضر بسته است.")
        return
    kb = []
    for key, plan in plans.items():
        p = price(key)
        kb.append([InlineKeyboardButton(f"{'📦' if ptype=='sub' else '🧪'} {plan['name']} — {fmt(p)}", callback_data=f"{'plan' if ptype=='sub' else 'test'}_{key}")])
    kb.append([InlineKeyboardButton("❌ لغو", callback_data="cancel")])
    title = "📋 *پلن‌های اشتراک*" if ptype == "sub" else "🧪 *اکانت تست*"
    await update.message.reply_text(title + "\n\nیکی از پلن‌های زیر را انتخاب کنید:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def show_invoice(q, uid, plan, plan_key, ptype):
    u = db.get_user(uid)
    bal = u["balance"] if u else 0
    p = plan["price"]
    text = (
        f"🧾 *فاکتور خرید*\n\n"
        f"📦 پلن: {plan['name']}\n"
        f"📊 حجم: {plan['size']}\n"
    )
    if plan.get("duration"):
        text += f"⏱ مدت: {plan['duration']}\n"
    text += f"💵 مبلغ: *{fmt(p)}*\n💰 موجودی شما: {fmt(bal)}\n\nروش پرداخت را انتخاب کنید:"

    key = f"{ptype}_{plan_key}"
    kb = [
        [InlineKeyboardButton("💳 پرداخت با کارت" + ("" if flag("card_open") else " (غیرفعال)"),
                              callback_data=f"pay_card_{key}")],
        [InlineKeyboardButton("💎 پرداخت با TON",
                              callback_data=f"pay_ton_{key}")],
        [InlineKeyboardButton(f"💰 پرداخت با موجودی {'✅' if bal>=p else '(ناکافی)'}",
                              callback_data=f"pay_wallet_{key}")],
        [InlineKeyboardButton("❌ لغو", callback_data="cancel")],
    ]
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def do_card_payment(q, uid, key, context):
    if not flag("card_open"):
        await q.edit_message_text("🔴 پرداخت کارت به کارت غیرفعال است.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ بستن", callback_data="cancel")]]))
        return
    parts = key.split("_", 1); ptype = parts[0]; plan_key = parts[1]
    plan = (config.PLANS if ptype == "sub" else config.TEST_PLANS).get(plan_key)
    if not plan: return
    p = price(plan_key)
    pay_id, inv = db.create_payment(uid, p, ptype, plan_key, plan["name"])
    ss(uid, {"w": "receipt", "pay_id": pay_id, "plan_key": plan_key, "plan_name": plan["name"], "ptype": ptype})
    await q.edit_message_text(
        f"💳 *اطلاعات پرداخت*\n\n"
        f"🔖 کد فاکتور: `{inv}`\n"
        f"📦 پلن: {plan['name']}\n"
        f"💵 مبلغ: *{fmt(p)}*\n\n"
        f"شماره کارت:\n`{card()}`\n"
        f"به نام: {cardholder()}\n\n"
        f"⏰ *{config.PAYMENT_TIMEOUT_MINUTES} دقیقه* فرصت دارید.\n"
        f"پس از واریز، تصویر رسید را ارسال کنید.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel")]])
    )
    asyncio.create_task(pay_timeout(context.bot, pay_id, uid, q.message.chat_id, config.PAYMENT_TIMEOUT_MINUTES * 60))

async def do_ton_payment(q, uid, key, context):
    wallet = ton_wallet()
    if not wallet:
        await q.edit_message_text(
            "⚠️ آدرس ولت TON هنوز توسط ادمین تنظیم نشده است.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ بستن", callback_data="cancel")]])
        )
        return
    parts = key.split("_", 1); ptype = parts[0]; plan_key = parts[1]
    plan = (config.PLANS if ptype == "sub" else config.TEST_PLANS).get(plan_key)
    if not plan: return
    p = price(plan_key)
    ton_amount = await toman_to_ton(p)
    if ton_amount <= 0:
        await q.edit_message_text(
            "⚠️ قیمت TON هنوز توسط ادمین تنظیم نشده است.\nلطفاً با روش دیگری پرداخت کنید.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ بستن", callback_data="cancel")]])
        )
        return
    pay_id, inv = db.create_payment(uid, p, ptype, plan_key, plan["name"])
    ss(uid, {"w": "receipt", "pay_id": pay_id, "plan_key": plan_key, "plan_name": plan["name"], "ptype": ptype, "method": "ton"})
    await q.edit_message_text(
        f"💎 *پرداخت با TON*\n\n"
        f"🔖 کد فاکتور: `{inv}`\n"
        f"📦 پلن: {plan['name']}\n"
        f"💵 مبلغ: *{fmt(p)}*\n\n"
        f"💎 معادل TON: *{ton_amount} TON*\n\n"
        f"آدرس ولت:\n`{wallet}`\n\n"
        f"⚠️ دقیقاً *{ton_amount} TON* واریز کنید.\n"
        f"⏰ *{config.PAYMENT_TIMEOUT_MINUTES} دقیقه* فرصت دارید.\n"
        f"پس از واریز، تصویر تراکنش را ارسال کنید.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel")]])
    )
    asyncio.create_task(pay_timeout(context.bot, pay_id, uid, q.message.chat_id, config.PAYMENT_TIMEOUT_MINUTES * 60))

async def do_wallet_payment(q, uid, key, context):
    parts = key.split("_", 1); ptype = parts[0]; plan_key = parts[1]
    plan = (config.PLANS if ptype == "sub" else config.TEST_PLANS).get(plan_key)
    if not plan: return
    p = price(plan_key)
    u = db.get_user(uid)
    if not u or u["balance"] < p:
        await q.edit_message_text("❌ موجودی کافی نیست.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ بستن", callback_data="cancel")]]))
        return

    db.update_balance(uid, -p)
    pay_id, inv = db.create_payment(uid, p, ptype, plan_key, plan["name"])

    # تلاش ارسال کانفیگ خودکار
    cfg = db.assign_config(plan_key, uid)
    db.confirm_payment(pay_id, cfg or "")
    db.create_subscription(uid, pay_id, plan_key, plan["name"], plan["size"], p, cfg or "")

    u = db.get_user(uid)

    if cfg:
        try:
            await context.bot.send_message(uid,
                f"✅ *خرید موفق!*\n\n🔖 فاکتور: `{inv}`\n📦 {plan['name']}\n\n🔑 کانفیگ:\n`{cfg}`",
                parse_mode="Markdown")
        except Exception: pass
        msg_to_admin = (
            f"🛍 *خرید با موجودی — کانفیگ ارسال شد*\n\n"
            f"{uinfo(u)}\n\n"
            f"🔖 فاکتور: `{inv}`\n📦 {plan['name']}\n💵 {fmt(p)}\n✅ کانفیگ ارسال شد"
        )
    else:
        try:
            await context.bot.send_message(uid,
                f"✅ پرداخت شما دریافت شد.\n🔖 فاکتور: `{inv}`\n\n"
                f"ممنون از پرداخت شما، کانفیگ شما به زودی ارسال خواهد شد.",
                parse_mode="Markdown")
        except Exception: pass
        msg_to_admin = (
            f"🛍 *خرید با موجودی — نیاز به ارسال کانفیگ*\n\n"
            f"{uinfo(u)}\n\n"
            f"🔖 فاکتور: `{inv}`\n📦 {plan['name']}\n💵 {fmt(p)}\n⚠️ کانفیگ موجود نبود"
        )

    for aid in all_admins():
        try:
            await context.bot.send_message(aid, msg_to_admin, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 ارسال کانفیگ دستی", callback_data=f"asc_{pay_id}")],
                    [InlineKeyboardButton("✉️ پیام مستقیم", callback_data=f"am_{uid}")],
                ]))
        except Exception: pass

    await q.edit_message_text(
        f"✅ پرداخت انجام شد.\n🔖 فاکتور: `{inv}`\n{'کانفیگ در پیام بعدی ارسال شد.' if cfg else 'کانفیگ شما به زودی ارسال می‌شود.'}",
        parse_mode="Markdown"
    )

# ── Receipt ───────────────────────────────────────────────

async def recv_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = gs(uid)
    pay_id = state.get("pay_id")
    if not pay_id: return

    pay = db.get_payment(pay_id)
    logger.info(f"recv_receipt: pay_id={pay_id}, pay={pay}")
    if not pay or pay["status"] in ("cancelled", "confirmed"):
        cs(uid)
        await update.message.reply_text("⚠️ این سفارش منقضی یا لغو شده است.", reply_markup=main_kb(uid))
        return

    msg = update.message
    if msg.photo:
        file_id = msg.photo[-1].file_id
        is_photo = True
    elif msg.document:
        file_id = msg.document.file_id
        is_photo = False
    else:
        await msg.reply_text("لطفاً تصویر رسید را به صورت عکس یا فایل ارسال کنید.")
        return

    db.set_receipt(pay_id, file_id, is_photo)
    plan_key = state.get("plan_key", "")
    plan_name = state.get("plan_name", "")
    u = db.get_user(uid)
    cs(uid)

    await msg.reply_text(
        "✅ رسید شما دریافت شد.\nپس از تایید، کانفیگ برایتان ارسال می‌شود.",
        reply_markup=main_kb(uid)
    )

    # ارسال به ادمین
    caption = (
        f"🧾 *رسید پرداخت جدید*\n\n"
        f"{uinfo(u)}\n\n"
        f"🔖 فاکتور: `{pay['invoice_code']}`\n"
        f"📦 پلن: {plan_name}\n"
        f"💵 مبلغ: {fmt(pay['amount'])}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید + ارسال کانفیگ", callback_data=f"ac_{pay_id}"),
         InlineKeyboardButton("❌ رد", callback_data=f"ar_{pay_id}")],
        [InlineKeyboardButton("📤 تایید + کانفیگ دستی", callback_data=f"asc_{pay_id}")],
        [InlineKeyboardButton("✉️ پیام مستقیم", callback_data=f"am_{uid}")],
    ])

    for aid in all_admins():
        try:
            if is_photo:
                await context.bot.send_photo(aid, photo=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
            else:
                await context.bot.send_document(aid, document=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            logger.error(f"send to admin {aid} failed: {e}")

# ── Admin confirm/reject ──────────────────────────────────

async def admin_confirm(q, pay_id, context):
    if not is_admin(q.from_user.id): return
    pay = db.get_payment(pay_id)
    if not pay:
        try: await q.edit_message_caption("⚠️ پرداخت یافت نشد.")
        except: await q.edit_message_text("⚠️ پرداخت یافت نشد.")
        return

    if pay["purpose"] == "topup":
        db.update_balance(pay["user_id"], pay["amount"])
        db.confirm_payment(pay_id)
        try:
            await context.bot.send_message(pay["user_id"],
                f"✅ موجودی کیف پول شما {fmt(pay['amount'])} افزایش یافت.")
        except Exception: pass
        try: await q.edit_message_caption(f"✅ شارژ `{pay['invoice_code']}` تایید شد.", parse_mode="Markdown")
        except: await q.edit_message_text(f"✅ شارژ `{pay['invoice_code']}` تایید شد.", parse_mode="Markdown")
        return

    # کانفیگ خودکار
    cfg = db.assign_config(pay["plan_key"], pay["user_id"])
    db.confirm_payment(pay_id, cfg or "")
    db.create_subscription(pay["user_id"], pay_id, pay["plan_key"], pay["plan_name"],
                           "", pay["amount"], cfg or "")

    if cfg:
        try:
            await context.bot.send_message(pay["user_id"],
                f"✅ *خرید موفق!*\n\n🔖 فاکتور: `{pay['invoice_code']}`\n📦 {pay['plan_name']}\n\n🔑 کانفیگ:\n`{cfg}`",
                parse_mode="Markdown")
        except Exception: pass
        result_text = f"✅ تایید شد — کانفیگ ارسال شد\nفاکتور: `{pay['invoice_code']}`"
    else:
        try:
            await context.bot.send_message(pay["user_id"],
                f"✅ پرداخت تایید شد.\n🔖 فاکتور: `{pay['invoice_code']}`\nکانفیگ شما به زودی ارسال می‌شود.",
                parse_mode="Markdown")
        except Exception: pass
        result_text = f"✅ تایید شد — ⚠️ کانفیگ موجود نبود\nفاکتور: `{pay['invoice_code']}`"

    try: await q.edit_message_caption(result_text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📤 ارسال کانفیگ دستی", callback_data=f"asc_{pay_id}")]]))
    except: await q.edit_message_text(result_text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📤 ارسال کانفیگ دستی", callback_data=f"asc_{pay_id}")]]))

async def admin_reject(q, pay_id, context):
    if not is_admin(q.from_user.id): return
    pay = db.get_payment(pay_id)
    db.cancel_payment(pay_id)
    if pay:
        try:
            await context.bot.send_message(pay["user_id"],
                f"❌ پرداخت شما (فاکتور `{pay['invoice_code']}`) تایید نشد.\nلطفاً با پشتیبانی تماس بگیرید.",
                parse_mode="Markdown")
        except Exception: pass
    try: await q.edit_message_caption(f"❌ رد شد — فاکتور `{pay['invoice_code']}`", parse_mode="Markdown")
    except: await q.edit_message_text(f"❌ رد شد — فاکتور `{pay['invoice_code']}`", parse_mode="Markdown")

# ── Admin send config manually ────────────────────────────

async def a_recv_send_cfg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = gs(uid)
    target = state.get("target")
    mode = state.get("mode")
    pay_id = state.get("pay_id")
    text = update.message.text.strip()
    cs(uid)

    if mode == "cfg":
        # ارسال کانفیگ دستی
        try:
            await context.bot.send_message(target,
                f"✅ *کانفیگ شما آماده است!*\n\n🔑 کانفیگ:\n`{text}`",
                parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("❌ ارسال ناموفق بود.", reply_markup=main_kb(uid))
            return
        if pay_id:
            db.confirm_payment(pay_id, text)
        await update.message.reply_text("✅ کانفیگ با موفقیت ارسال شد.", reply_markup=main_kb(uid))
    else:
        # پیام مستقیم
        try:
            await context.bot.send_message(target, f"📨 *پیام از پشتیبانی:*\n\n{text}", parse_mode="Markdown")
            await update.message.reply_text("✅ پیام ارسال شد.", reply_markup=main_kb(uid))
        except Exception:
            await update.message.reply_text("❌ ارسال ناموفق.", reply_markup=main_kb(uid))

# ── Referral ──────────────────────────────────────────────

async def show_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = db.get_user(uid)
    if not u: return
    bot_info = await context.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={u['referral_code']}"
    rem = max(0, config.REFERRAL_THRESHOLD - u["referral_count"])
    await update.message.reply_text(
        f"👥 *زیرمجموعه‌گیری*\n\n"
        f"🔗 لینک اختصاصی:\n`{link}`\n\n"
        f"👫 دعوت‌های موفق: {u['referral_count']}\n"
        f"🎁 تا کانفیگ رایگان: {rem} نفر دیگر\n\n"
        f"هر {config.REFERRAL_THRESHOLD} دعوت = یک کانفیگ تست رایگان",
        parse_mode="Markdown"
    )

# ── Support ───────────────────────────────────────────────

async def start_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ss(uid, {"w": "support"})
    await update.message.reply_text("🎧 پیام خود را بنویسید:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel")]]))

async def recv_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = db.get_user(uid)
    msg = update.message.text
    cs(uid)
    await update.message.reply_text("✅ پیام شما ارسال شد.", reply_markup=main_kb(uid))
    for aid in all_admins():
        try:
            await context.bot.send_message(aid,
                f"📩 *پشتیبانی*\n\n{uinfo(u)}\n\n💬 {msg}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✉️ پاسخ", callback_data=f"am_{uid}")]]))
        except Exception: pass

# ── Account ───────────────────────────────────────────────

async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = db.get_user(uid)
    if not u: return
    subs = db.get_user_subscriptions(uid)
    sub_text = ""
    for s in subs[:10]:
        cfg_short = f"\n   🔑 `{s['config_sent'][:40]}...`" if s.get("config_sent") else "\n   ⏳ در انتظار ارسال کانفیگ"
        sub_text += f"\n\n📦 {s['plan_name']} — {s['created_at'][:10]}{cfg_short}"
    if not sub_text:
        sub_text = "\nاشتراکی یافت نشد."
    await update.message.reply_text(
        f"👤 *حساب من*\n\n"
        f"💰 موجودی: {fmt(u['balance'])}\n"
        f"👥 دعوت‌ها: {u['referral_count']}\n\n"
        f"📋 *اشتراک‌های من:*{sub_text}",
        parse_mode="Markdown"
    )

# ── Top-up ────────────────────────────────────────────────

async def start_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not flag("topup_open"):
        await update.message.reply_text("🔴 افزایش موجودی در حال حاضر غیرفعال است.")
        return
    ss(uid, {"w": "topup_amount"})
    await update.message.reply_text(
        "💳 مبلغ شارژ را وارد کنید (۵۰,۰۰۰ تا ۵,۰۰۰,۰۰۰ تومان):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel")]]))

async def start_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not flag("topup_open"):
        await update.message.reply_text("🔴 افزایش موجودی در حال حاضر غیرفعال است.")
        return
    await update.message.reply_text(
        "💳 روش افزایش موجودی را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 کارت به کارت", callback_data="topup_card")],
            [InlineKeyboardButton("💎 پرداخت با TON", callback_data="topup_ton")],
            [InlineKeyboardButton("❌ لغو", callback_data="cancel")],
        ])
    )

async def recv_topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = gs(uid)
    method = state.get("method", "card")
    try:
        amount = int(update.message.text.replace(",", "").replace("،", "").strip())
    except ValueError:
        await update.message.reply_text("⚠️ لطفاً یک عدد وارد کنید."); return
    if amount < 50000:
        await update.message.reply_text("⚠️ حداقل ۵۰,۰۰۰ تومان."); return
    if amount > 5000000:
        await update.message.reply_text("⚠️ حداکثر ۵,۰۰۰,۰۰۰ تومان."); return

    pay_id, inv = db.create_payment(uid, amount, "topup")

    if method == "ton":
        ton_amount = await toman_to_ton(amount)
        wallet = ton_wallet()
        ss(uid, {"w": "topup_receipt", "pay_id": pay_id})
        await update.message.reply_text(
            f"🧾 *فاکتور شارژ با TON*\n\n"
            f"🔖 کد فاکتور: `{inv}`\n"
            f"💵 مبلغ: *{fmt(amount)}*\n"
            f"💎 معادل TON: *{ton_amount} TON*\n\n"
            f"آدرس ولت:\n`{wallet}`\n\n"
            f"⚠️ دقیقاً *{ton_amount} TON* واریز کنید.\n"
            f"⏰ {config.PAYMENT_TIMEOUT_MINUTES} دقیقه فرصت دارید.\nتصویر تراکنش را ارسال کنید.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel")]]))
    else:
        ss(uid, {"w": "topup_receipt", "pay_id": pay_id})
        await update.message.reply_text(
            f"🧾 *فاکتور شارژ کیف پول*\n\n"
            f"🔖 کد فاکتور: `{inv}`\n"
            f"💵 مبلغ: *{fmt(amount)}*\n\n"
            f"💳 شماره کارت:\n`{card()}`\nبه نام: {cardholder()}\n\n"
            f"⏰ {config.PAYMENT_TIMEOUT_MINUTES} دقیقه فرصت دارید.\nتصویر رسید را ارسال کنید.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel")]]))
    asyncio.create_task(pay_timeout(context.bot, pay_id, uid, update.effective_chat.id, config.PAYMENT_TIMEOUT_MINUTES * 60))

async def recv_topup_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = gs(uid)
    pay_id = state.get("pay_id")
    if not pay_id: return
    msg = update.message
    if msg.photo:
        file_id = msg.photo[-1].file_id; is_photo = True
    elif msg.document:
        file_id = msg.document.file_id; is_photo = False
    else:
        await msg.reply_text("لطفاً تصویر رسید را ارسال کنید."); return

    db.set_receipt(pay_id, file_id, is_photo)
    pay = db.get_payment(pay_id)
    if not pay or pay["status"] in ("cancelled", "confirmed"):
        cs(uid)
        await update.message.reply_text("⚠️ این سفارش منقضی یا لغو شده است.", reply_markup=main_kb(uid))
        return
    u = db.get_user(uid)
    cs(uid)
    await msg.reply_text("✅ رسید دریافت شد. پس از تایید موجودی افزایش می‌یابد.", reply_markup=main_kb(uid))

    caption = (
        f"💳 *درخواست شارژ کیف پول*\n\n"
        f"{uinfo(u)}\n\n"
        f"🔖 فاکتور: `{pay['invoice_code']}`\n"
        f"💵 مبلغ: {fmt(pay['amount'])}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید", callback_data=f"ac_{pay_id}"),
         InlineKeyboardButton("❌ رد", callback_data=f"ar_{pay_id}")],
        [InlineKeyboardButton("✉️ پیام مستقیم", callback_data=f"am_{uid}")],
    ])
    for aid in all_admins():
        try:
            if is_photo:
                await context.bot.send_photo(aid, photo=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
            else:
                await context.bot.send_document(aid, document=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            logger.error(f"topup receipt to admin {aid}: {e}")

# ── Admin panel ───────────────────────────────────────────

async def show_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text("🔧 *پنل مدیریت*", parse_mode="Markdown", reply_markup=admin_kb())

async def a_show_users(q):
    users = db.get_all_users()
    text = f"👥 *کاربران ({len(users)} نفر)*\n\n"
    for u in users[:20]:
        un = f"@{u['username']}" if u.get("username") else "—"
        text += f"• {u['full_name']} | {un} | {fmt(u['balance'])}\n"
    if len(users) > 20: text += f"\n... و {len(users)-20} نفر دیگر"
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())

async def a_show_pays(q):
    pays = db.get_pending_payments()
    if not pays:
        await q.edit_message_text("✅ پرداخت در انتظاری وجود ندارد.", reply_markup=back_kb()); return
    text = f"💰 *در انتظار تایید ({len(pays)})*\n\n"
    for p in pays:
        un = f"@{p['username']}" if p.get("username") else "—"
        text += f"• `{p['invoice_code']}` | {p['full_name']} | {un} | {fmt(p['amount'])}\n"
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())

async def a_show_configs(q, uid):
    summary = db.get_configs_summary()
    counts = {r["plan_key"]: r["available"] for r in summary}
    all_keys = list(config.PLANS.keys()) + list(config.TEST_PLANS.keys()) + ["referral"]
    text = "📦 *موجودی کانفیگ‌ها*\n\n"
    for k in all_keys:
        text += f"• {PLAN_LABELS.get(k,k)}: {counts.get(k,0)} عدد\n"
    kb = [[InlineKeyboardButton(f"➕ {PLAN_LABELS.get(k,k)}", callback_data=f"a_addcfg_{k}")] for k in all_keys]
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def a_show_prices(q, uid):
    all_plans = {**config.PLANS, **config.TEST_PLANS}
    text = "💲 *قیمت‌های فعلی*\n\n"
    for k, plan in all_plans.items():
        text += f"• {plan['name']}: {fmt(price(k))}\n"
    kb = [[InlineKeyboardButton(f"✏️ {plan['name']}", callback_data=f"a_setprice_{k}")] for k, plan in all_plans.items()]
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def a_show_admins(q, uid):
    aids = db.get_admin_ids()
    text = "👤 *ادمین‌ها*\n\n" + "\n".join([f"• `{a}`" for a in aids]) if aids else "👤 *ادمین‌ها*\n\nهیچ ادمینی ثبت نشده"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن", callback_data="a_add_admin"),
         InlineKeyboardButton("➖ حذف", callback_data="a_del_admin")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")],
    ])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

# ── Admin input handlers ──────────────────────────────────

async def a_recv_bal_uid(update, context):
    uid = update.effective_user.id
    try:
        tid = int(update.message.text.strip())
        t = db.get_user(tid)
        if not t: await update.message.reply_text("⚠️ کاربر یافت نشد."); return
        ss(uid, {"w": "a_bal_amt", "tid": tid})
        await update.message.reply_text(f"موجودی فعلی: {fmt(t['balance'])}\nمقدار تغییر (مثبت/منفی):")
    except ValueError:
        await update.message.reply_text("⚠️ آیدی باید عدد باشد.")

async def a_recv_bal_amt(update, context):
    uid = update.effective_user.id
    state = gs(uid)
    tid = state.get("tid")
    try:
        delta = int(update.message.text.strip().replace(",", ""))
        db.update_balance(tid, delta)
        t = db.get_user(tid)
        cs(uid)
        await update.message.reply_text(f"✅ موجودی به‌روز شد: {fmt(t['balance'])}", reply_markup=main_kb(uid))
        try:
            await context.bot.send_message(tid, f"💰 موجودی کیف پول شما تغییر کرد.\nموجودی جدید: {fmt(t['balance'])}")
        except Exception: pass
    except ValueError:
        await update.message.reply_text("⚠️ عدد وارد کنید.")

async def a_recv_price(update, context):
    uid = update.effective_user.id
    state = gs(uid)
    key = state.get("key")
    try:
        p = int(update.message.text.strip().replace(",", ""))
        db.set_setting(f"price_{key}", str(p))
        cs(uid)
        await update.message.reply_text(f"✅ قیمت {PLAN_LABELS.get(key,key)} → {fmt(p)}", reply_markup=main_kb(uid))
    except ValueError:
        await update.message.reply_text("⚠️ عدد وارد کنید.")

async def a_recv_configs(update, context):
    uid = update.effective_user.id
    state = gs(uid)
    plan_key = state.get("plan_key", "referral")
    lines = [l.strip() for l in update.message.text.strip().split("\n") if l.strip()]
    if not lines:
        await update.message.reply_text("⚠️ هیچ کانفیگی یافت نشد."); return
    db.add_configs(plan_key, lines)
    cs(uid)
    cnt = db.get_config_count(plan_key)
    await update.message.reply_text(
        f"✅ {len(lines)} کانفیگ برای «{PLAN_LABELS.get(plan_key,plan_key)}» اضافه شد.\n📊 موجودی: {cnt}",
        reply_markup=main_kb(uid))

async def a_recv_broadcast(update, context):
    uid = update.effective_user.id
    text = update.message.text.strip()
    cs(uid)
    ids = db.get_all_user_ids()
    ok = fail = 0
    for i in ids:
        try:
            await context.bot.send_message(i, f"📢 *پیام مدیریت:*\n\n{text}", parse_mode="Markdown")
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"📢 ارسال شد.\n✅ {ok} موفق | ❌ {fail} ناموفق", reply_markup=main_kb(uid))

async def a_recv_add_admin(update, context):
    uid = update.effective_user.id
    try:
        nid = int(update.message.text.strip())
        db.add_admin(nid)
        cs(uid)
        await update.message.reply_text(f"✅ ادمین {nid} اضافه شد.", reply_markup=main_kb(uid))
    except ValueError:
        await update.message.reply_text("⚠️ آیدی باید عدد باشد.")

async def a_recv_del_admin(update, context):
    uid = update.effective_user.id
    try:
        rid = int(update.message.text.strip())
        if rid in config.ADMIN_IDS:
            await update.message.reply_text("⚠️ ادمین اصلی قابل حذف نیست."); return
        db.remove_admin(rid)
        cs(uid)
        await update.message.reply_text(f"✅ ادمین {rid} حذف شد.", reply_markup=main_kb(uid))
    except ValueError:
        await update.message.reply_text("⚠️ آیدی باید عدد باشد.")

async def a_recv_card(update, context):
    uid = update.effective_user.id
    state = gs(uid)
    step = state.get("step", "number")
    if step == "number":
        c_num = update.message.text.strip()
        db.set_setting("card_number", c_num)
        ss(uid, {"w": "a_card", "step": "holder"})
        await update.message.reply_text(
            f"✅ شماره کارت ذخیره شد: `{c_num}`\n\nحالا نام صاحب کارت را وارد کنید:\n(فعلی: {cardholder()})",
            parse_mode="Markdown"
        )
    elif step == "holder":
        holder = update.message.text.strip()
        db.set_setting("card_holder", holder)
        cs(uid)
        await update.message.reply_text(
            f"✅ اطلاعات کارت به‌روز شد:\n💳 شماره: `{card()}`\n👤 نام: {holder}",
            parse_mode="Markdown", reply_markup=main_kb(uid)
        )

async def a_recv_wallet(update, context):
    uid = update.effective_user.id
    addr = update.message.text.strip()
    db.set_setting("ton_wallet_address", addr)
    cs(uid)
    await update.message.reply_text(
        f"✅ آدرس ولت TON به‌روز شد:\n`{addr}`",
        parse_mode="Markdown", reply_markup=main_kb(uid)
    )

async def a_recv_ton_price(update, context):
    uid = update.effective_user.id
    try:
        p = float(update.message.text.strip().replace(",", "").replace("،", ""))
        db.set_setting("ton_price_toman", str(p))
        cs(uid)
        await update.message.reply_text(
            f"✅ قیمت TON به‌روز شد:\n💲 هر TON = `{p:,.0f}` تومان",
            parse_mode="Markdown", reply_markup=main_kb(uid)
        )
    except ValueError:
        await update.message.reply_text("⚠️ لطفاً یک عدد وارد کنید. مثال: 428000")



# ── Unified message handler ──────────────────────────────

async def on_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """همه پیام‌ها از اینجا رد میشن"""
    if not update.message:
        return
    uid = update.effective_user.id
    msg = update.message

    # لاگ برای دیباگ
    has_photo = bool(msg.photo)
    has_doc = bool(msg.document)
    has_text = bool(msg.text)
    logger.info(f"on_any_message: uid={uid}, photo={has_photo}, doc={has_doc}, text={has_text}")

    state = gs(uid)
    w = state.get("w")
    logger.info(f"on_any_message: state_w={w}")

    if (has_photo or has_doc) and w in ("receipt", "topup_receipt"):
        if w == "receipt":
            await recv_receipt(update, context)
        else:
            await recv_topup_receipt(update, context)
        return

    if has_text:
        await on_message(update, context)

# ── Commands ──────────────────────────────────────────────

async def cmd_admin(update, context):
    if is_admin(update.effective_user.id):
        await show_admin(update, context)

async def cmd_setbalance(update, context):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 2:
        await update.message.reply_text("استفاده: /setbalance <uid> <amount>"); return
    try:
        db.set_balance(int(context.args[0]), int(context.args[1]))
        await update.message.reply_text("✅ موجودی تنظیم شد.")
    except ValueError:
        await update.message.reply_text("⚠️ مقادیر نامعتبر.")

# ── Main ──────────────────────────────────────────────────

def main():
    db.init_db()
    for aid in config.ADMIN_IDS:
        db.add_admin(aid)

    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("setbalance", cmd_setbalance))
    app.add_handler(CallbackQueryHandler(on_callback))
    # یک handler برای همه پیام‌ها
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_any_message))

    logger.info("Bot started.")
    app.run_polling(
        drop_pending_updates=False,
        allowed_updates=["message", "callback_query", "channel_post", "edited_message"]
    )

if __name__ == "__main__":
    main()
