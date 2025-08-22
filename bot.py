import asyncio
import logging
import secrets
import string
from datetime import datetime, timedelta
import random
import aiosqlite
import requests
from telegram import Update, BotCommand, BotCommandScopeChat
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import nest_asyncio

# -------------------- 配置 --------------------
TOKEN = '你TG BOT的API'
JELLYFIN_URL = 'http://XXXXX:8097'
ADMIN_API_KEY = 'XXXXXXXXXXXXXXXXXXXXXXXXX'
ADMIN_IDS = {XXXXXXXX}
DB_PATH = 'bot_data.db'

DAILY_COIN_MIN = 10
DAILY_COIN_MAX = 50
INVITE_COIN_REWARD = 100
KEEP_ALIVE_COINS = 100
# ----------------------------------------------

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- 数据库初始化 ----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS invites (
            code TEXT PRIMARY KEY,
            type TEXT CHECK(type IN ('1d','1m','1y','perm')),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            registered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            tg_id INTEGER UNIQUE,
            expires_at DATETIME,
            whitelisted INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS user_currency (
            tg_id INTEGER PRIMARY KEY,
            coins INTEGER DEFAULT 0,
            last_daily DATETIME,
            invited_by INTEGER,
            FOREIGN KEY (tg_id) REFERENCES users(tg_id)
        );
        CREATE TABLE IF NOT EXISTS exchange_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            description TEXT NOT NULL,
            enabled INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS user_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            purchase_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            used INTEGER DEFAULT 0,
            FOREIGN KEY (tg_id) REFERENCES users(tg_id),
            FOREIGN KEY (item_id) REFERENCES exchange_items(id)
        );
        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_expires ON users(expires_at);
        CREATE INDEX IF NOT EXISTS idx_tg_id ON users(tg_id);
        CREATE INDEX IF NOT EXISTS idx_last_daily ON user_currency(last_daily);
        CREATE INDEX IF NOT EXISTS idx_whitelisted ON users(whitelisted);
        ''')
        await db.commit()

        cur = await db.execute("SELECT COUNT(*) FROM exchange_items")
        if (await cur.fetchone())[0] == 0:
            await db.executemany(
                "INSERT INTO exchange_items(name,price,description) VALUES(?,?,?)",
                [('白名单', 9999, '永久白名单'),
                 ('解除群内封禁', 1, '解封'),
                 ('求片机会', 10, '一次求片')]
            )
            await db.commit()

        cur = await db.execute("SELECT COUNT(*) FROM bot_config")
        if (await cur.fetchone())[0] == 0:
            await db.executemany(
                "INSERT INTO bot_config(key,value) VALUES(?,?)",
                [('daily_coin_min', str(DAILY_COIN_MIN)),
                 ('daily_coin_max', str(DAILY_COIN_MAX)),
                 ('invite_coin_reward', str(INVITE_COIN_REWARD)),
                 ('keep_alive_coins', str(KEEP_ALIVE_COINS)),
                 ('group_id', '0'),
                 ('registration_notice', '1'),
                 ('self_registration', '1')]
            )
            await db.commit()

# ---------- 通用 ----------
async def get_config(key, default=None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM bot_config WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else default

async def set_config(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("REPLACE INTO bot_config(key,value) VALUES(?,?)", (key, str(value)))
        await db.commit()

async def is_user_in_group(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    gid = await get_config('group_id', '0')
    if gid == '-1003079851347':
        return True
    try:
        m = await context.bot.get_chat_member(chat_id=gid, user_id=user_id)
        return m.status in ['member', 'administrator', 'creator']
    except:
        return False

def get_jellyfin_user_id(username: str) -> str | None:
    try:
        r = requests.get(f"{JELLYFIN_URL}/Users", headers={'X-Emby-Token': ADMIN_API_KEY}, timeout=10)
        for u in r.json():
            if u['Name'] == username:
                return u['Id']
    except:
        pass
    return None

def register_jellyfin_user(username: str, password: str) -> bool:
    headers = {'X-Emby-Token': ADMIN_API_KEY, 'Content-Type': 'application/json'}
    payload = {
        "Name": username,
        "Password": password,
        "Policy": {
            "IsAdministrator": False,
            "IsDisabled": False,
            "EnableContentDownloading": True,
            "EnableAllFolders": True
        }
    }
    try:
        r = requests.post(f"{JELLYFIN_URL}/Users/New", json=payload, headers=headers, timeout=10)
        return r.status_code == 200
    except:
        return False

# ---------- 货币 ----------
async def get_user_currency(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT coins,last_daily,invited_by FROM user_currency WHERE tg_id=?", (tg_id,)) as cur:
            row = await cur.fetchone()
    if row:
        return {"coins": row[0], "last_daily": datetime.fromisoformat(row[1]) if row[1] else None, "invited_by": row[2]}
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO user_currency(tg_id,coins,last_daily) VALUES(?,0,NULL)", (tg_id,))
            await db.commit()
        return {"coins": 0, "last_daily": None, "invited_by": None}

async def update_user_currency(tg_id: int, coins=None, last_daily=None, invited_by=None):
    sets, params = [], []
    if coins is not None:
        sets.append("coins=?"); params.append(coins)
    if last_daily is not None:
        sets.append("last_daily=?"); params.append(last_daily.isoformat() if last_daily else None)
    if invited_by is not None:
        sets.append("invited_by=?"); params.append(invited_by)
    if sets:
        params.append(tg_id)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(f"UPDATE user_currency SET {','.join(sets)} WHERE tg_id=?", params)
            await db.commit()

# ---------- 自动清理 ----------
async def auto_delete_expired_accounts():
    while True:
        try:
            now = datetime.utcnow()
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                    SELECT username FROM users
                    WHERE expires_at < ? AND expires_at IS NOT NULL AND whitelisted=0
                """, (now,)) as cur:
                    expired = [r[0] for r in await cur.fetchall()]
            for u in expired:
                if uid := get_jellyfin_user_id(u):
                    requests.delete(f"{JELLYFIN_URL}/Users/{uid}", headers={'X-Emby-Token': ADMIN_API_KEY}, timeout=5)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM users WHERE username=?", (u,))
                    await db.execute("DELETE FROM user_currency WHERE tg_id IN (SELECT tg_id FROM users WHERE username=?)", (u,))
                    await db.commit()
                logger.info(f"已删除过期账号: {u}")
            await asyncio.sleep(3600)
        except Exception as e:
            logger.error(f"清理异常: {e}")
            await asyncio.sleep(300)

# ---------- 命令 ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    invite_reward = int(await get_config('invite_coin_reward', INVITE_COIN_REWARD))
    keep_alive = int(await get_config('keep_alive_coins', KEEP_ALIVE_COINS))
    await update.message.reply_text(
        "🎉 欢迎使用Jellyfin账号机器人\n\n"
        "🔑 注册账号：/register <用户名> <密码>\n"
        "🔍 查询信息：/query_credentials\n"
        "💰 签到赚币：/daily\n"
        "🪙 查看余额：/balance\n"
        "🛒 兑换商店：/shop\n"
        f"💡 邀请好友奖励：成功邀请一位好友可获得 {invite_reward} 星海币！\n"
        f"🔔 保号要求：需要至少 {keep_alive} 星海币保持账号活跃"
    )

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_user_in_group(context, user_id):
        await update.message.reply_text("❌ 您需要加入指定群组才能使用本机器人")
        return

    allow = await get_config('self_registration', '1') == '1'

    # 关闭自助注册时必须提供邀请码
    if not allow:
        if not context.args or not (context.args[0].startswith('inv_') and len(context.args[0]) == 14):
            await update.message.reply_text(
                "❌ 自助注册已关闭，请联系管理员获取邀请码或在群内申请开通。"
            )
            return
        code = context.args[0][4:]
        ok, exp = await validate_invite(code)
        if not ok:
            await update.message.reply_text("❌ 邀请码无效或已过期")
            return
        # 移除邀请码
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM invites WHERE code=?", (code,))
            await db.commit()

    # 参数提取
    if not allow:
        if len(context.args) < 3:
            await update.message.reply_text("📝 格式：/register <邀请码> <用户名> <密码>")
            return
        username, password = context.args[1], context.args[2]
    else:
        if len(context.args) < 2:
            await update.message.reply_text("📝 格式：/register <用户名> <密码>")
            return
        username, password = context.args[0], context.args[1]

    # 重复注册检查
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT username FROM users WHERE tg_id=?", (user_id,)) as cur:
            if await cur.fetchone():
                await update.message.reply_text("⚠️ 您已注册过账号")
                return
    if len(password) < 6:
        await update.message.reply_text("⚠️ 密码至少6位")
        return
    if not await asyncio.to_thread(register_jellyfin_user, username, password):
        await update.message.reply_text("🔧 注册失败，请联系管理员")
        return

    expires = (datetime.utcnow() + timedelta(days=30)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users(username,password,tg_id,expires_at) VALUES(?,?,?,?)",
            (username, password, user_id, expires)
        )
        await db.commit()

    await update.message.reply_text(
        f"✅ 注册成功！\n👤 用户名：{username}\n🔒 密码：{password}\n⏰ 到期：{expires}"
    )

async def query_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_user_in_group(context, user_id):
        await update.message.reply_text("❌ 您需要加入指定群组才能使用此功能")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT username, password, expires_at, whitelisted FROM users WHERE tg_id=?", (user_id,)) as cur:
            rows = await cur.fetchall()
    if not rows:
        await update.message.reply_text("❌ 您尚未注册任何账号")
        return
    msg = ["📋 您的账号信息"]
    for u, p, exp, wl in rows:
        exp_info = "永久" if not exp else datetime.fromisoformat(exp).strftime('%Y-%m-%d %H:%M:%S UTC')
        wl_info = "⭐白名单用户" if wl else ""
        msg.append(
            f"👤 用户名：{u}\n"
            f"🔒 密码：{p}\n"
            f"⏰ 到期：{exp_info}\n"
            f"{wl_info}\n"
            f"—————————————"
        )
    await update.message.reply_text("\n".join(msg))

async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_user_in_group(context, user_id):
        await update.message.reply_text("❌ 您需要加入指定群组才能使用此功能")
        return
    info = await get_user_currency(user_id)
    now = datetime.utcnow()
    if info["last_daily"] and info["last_daily"].date() == now.date():
        next_daily = (info["last_daily"] + timedelta(days=1)).replace(hour=0, minute=0, second=0)
        wait_time = next_daily - now
        hours = wait_time.seconds // 3600
        minutes = (wait_time.seconds % 3600) // 60
        await update.message.reply_text(
            f"⏳ 今日已签到，请 {hours} 小时 {minutes} 分钟后再来"
        )
        return
    coins = random.randint(DAILY_COIN_MIN, DAILY_COIN_MAX)
    new_bal = info["coins"] + coins
    await update_user_currency(user_id, coins=new_bal, last_daily=now)
    await update.message.reply_text(
        f"🎉 签到成功！获得 {coins} 星海币\n"
        f"💰 当前余额：{new_bal} 星海币\n\n"
        "使用 /shop 查看可兑换的商品"
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_user_in_group(context, user_id):
        await update.message.reply_text("❌ 您需要加入指定群组才能使用此功能")
        return
    info = await get_user_currency(user_id)
    daily_min = int(await get_config('daily_coin_min', DAILY_COIN_MIN))
    daily_max = int(await get_config('daily_coin_max', DAILY_COIN_MAX))
    invite_reward = int(await get_config('invite_coin_reward', INVITE_COIN_REWARD))
    keep_alive = int(await get_config('keep_alive_coins', KEEP_ALIVE_COINS))
    await update.message.reply_text(
        f"💰 您的余额：{info['coins']} 星海币\n"
        f"✅ 保号要求：需要至少 {keep_alive} 星海币\n\n"
        "💡 获取更多星海币：\n"
        f"- 每日签到：/daily (可获得 {daily_min}-{daily_max} 星海币)\n"
        f"- 邀请好友：每位成功注册的好友奖励 {invite_reward} 星海币\n\n"
        "使用 /shop 查看可兑换的商品"
    )

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_user_in_group(context, user_id):
        await update.message.reply_text("❌ 您需要加入指定群组才能使用此功能")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id,name,price,description FROM exchange_items WHERE enabled=1 ORDER BY price") as cur:
            items = await cur.fetchall()
    if not items:
        await update.message.reply_text("🛒 商店暂无商品")
        return
    info = await get_user_currency(user_id)
    msg = ["🛒 兑换商店"]
    for item_id, name, price, desc in items:
        msg.append(f"\n{name} - {price} 星海币")
        msg.append(f"   {desc}")
        msg.append(f"   /buy_{item_id} 兑换")
    msg.append(f"\n💰 您的余额：{info['coins']} 星海币")
    await update.message.reply_text("\n".join(msg))

async def handle_buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        item_id = int(update.message.text.split('_')[1])
    except (ValueError, IndexError):
        await update.message.reply_text("❌ 无效的购买命令")
        return

    user_id = update.effective_user.id
    if not await is_user_in_group(context, user_id):
        await update.message.reply_text("❌ 您需要加入指定群组才能使用此功能")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id,name,price,description FROM exchange_items WHERE id=? AND enabled=1", (item_id,)) as cur:
            item = await cur.fetchone()
    if not item:
        await update.message.reply_text("❌ 商品不存在或已下架")
        return
    item_id, name, price, description = item

    info = await get_user_currency(user_id)
    if info["coins"] < price:
        await update.message.reply_text(
            f"❌ 星海币不足，需要 {price} 星海币，您只有 {info['coins']} 星海币"
        )
        return

    # 扣除星海币并记录
    new_balance = info["coins"] - price
    await update_user_currency(user_id, coins=new_balance)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO user_purchases(tg_id,item_id) VALUES(?,?)", (user_id, item_id))
        await db.commit()

    await update.message.reply_text(
        f"✅ 兑换成功！\n"
        f"🎁 商品：{name}\n"
        f"📝 描述：{description}\n"
        f"💰 剩余星海币：{new_balance}"
    )

async def toggle_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ 权限不足")
        return
    current = int(await get_config('self_registration', 1))
    new_val = 1 - current
    await set_config('self_registration', new_val)
    status = "开启" if new_val else "关闭"
    await update.message.reply_text(f"✅ 自助注册已{status}")

# ---------- 主 ----------
async def main():
    await init_db()
    app = ApplicationBuilder().token(TOKEN).build()
    asyncio.create_task(auto_delete_expired_accounts())

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("query_credentials", query_credentials))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("shop", shop))
    for i in (1, 2, 3):
        app.add_handler(CommandHandler(f"buy_{i}", handle_buy_command))
    app.add_handler(CommandHandler("toggle_registration", toggle_registration))

    # 公开菜单
    await app.bot.set_my_commands([
        BotCommand("start", "显示帮助信息"),
        BotCommand("register", "注册新账号"),
        BotCommand("query_credentials", "查询账号信息"),
        BotCommand("daily", "每日签到赚星海币"),
        BotCommand("balance", "查看星海币余额"),
        BotCommand("shop", "查看兑换商店")
    ])

    # 管理员菜单
    admin_commands = [
        BotCommand("toggle_registration", "开关自助注册"),
        BotCommand("kk", "查看/给予用户资格"),
        BotCommand("give", "直接给账号"),
        BotCommand("set_group", "设置群组ID"),
        BotCommand("generate_invite", "生成邀请码"),
        BotCommand("admin_accounts", "查看所有账号"),
        BotCommand("delete_account", "删除账号")
    ]
    for aid in ADMIN_IDS:
        try:
            await app.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(aid))
        except Exception as e:
            logger.error(f"无法设置管理员命令: {e}")

    logger.info("Bot started")
    await app.run_polling()

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())
