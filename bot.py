import json
import os
import random
import threading
from collections import Counter
from http.server import HTTPServer, BaseHTTPRequestHandler

import discord
from discord import app_commands
from discord.ext import commands

# --- RENDER KEEP-ALIVE SERVER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, format, *args):
        # Silence HTTP logs to keep console output clean
        return

def run_health_check_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# Start background thread for Render port binding
threading.Thread(target=run_health_check_server, daemon=True).start()


# --- BOT CONFIGURATION ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.dm_messages = True
intents.members = True       # Required to track member joins
intents.invites = True       # Required to track invites

bot = commands.Bot(command_prefix="!", intents=intents)

# Load TOKEN from Environment Variables
TOKEN = os.environ.get("DISCORD_TOKEN")

# Channel IDs
WELCOME_CHANNEL_ID = 1547265722525290536       # Welcome Channel ID
STAFF_CHANNEL_ID = 1543969779591815333          # Initial Confessions Audit Log
PUBLIC_CHANNEL_ID = 1547265722525290536         # Public Anonymous Log
STAFF_REPLIES_CHANNEL_ID = 1544215871885541386     # Staff Replies Log ONLY

MSG_MAP_FILE = "message_map.json"
USER_ACTIVE_FILE = "user_active_threads.json"
COUNTER_FILE = "counter.json"

# In-memory invite tracker: {guild_id: {invite_code: uses}}
invites_cache = {}


# --- GAKURAN STYLES DATA & PROBABILITIES ---

STYLES_DATA = [
    ("Basic", "Common", 59.0, discord.Color.light_gray()),
    ("Slugger", "Uncommon", 7.5, discord.Color.green()),
    ("Muay Thai", "Uncommon", 7.5, discord.Color.green()),
    ("Karate", "Uncommon", 7.5, discord.Color.green()),
    ("Kickboxing", "Uncommon", 7.5, discord.Color.green()),
    ("Boxing", "Epic", 5.0, discord.Color.purple()),
    ("Striker", "Epic", 5.0, discord.Color.purple()),
    ("Capoeira", "Legendary", 0.20, discord.Color.gold()),
    ("Wrestling", "Legendary", 0.20, discord.Color.gold()),
    ("Kure", "Legendary", 0.20, discord.Color.gold()),
    ("Hakari", "Legendary", 0.20, discord.Color.gold()),
    ("Kyoushin Karate", "Legendary", 0.20, discord.Color.gold()),
    ("Ali", "???", 0.025, discord.Color.from_rgb(255, 20, 147)),
    ("WingChun", "???", 0.025, discord.Color.from_rgb(255, 20, 147)),
    ("CQC", "???", 0.025, discord.Color.from_rgb(255, 20, 147)),
]

STYLE_NAMES = [s[0] for s in STYLES_DATA]
STYLE_TIERS = {s[0]: s[1] for s in STYLES_DATA}
STYLE_CHANCES = {s[0]: s[2] for s in STYLES_DATA}
STYLE_WEIGHTS = [s[2] for s in STYLES_DATA]
STYLE_COLORS = {s[0]: s[3] for s in STYLES_DATA}

LEGENDARY_OR_ABOVE = {"Legendary", "???"}


# --- PERSISTENT STORAGE MANAGEMENT ---

def load_data(file_path):
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            return json.load(f)
    return {} if "json" in file_path and file_path != COUNTER_FILE else {"count": 0}

def save_data(data, file_path):
    with open(file_path, "w") as f:
        json.dump(data, f, indent=4)

message_map = load_data(MSG_MAP_FILE)
user_last_thread = load_data(USER_ACTIVE_FILE)
counter_data = load_data(COUNTER_FILE)


def get_next_confession_number():
    counter_data["count"] += 1
    save_data(counter_data, COUNTER_FILE)
    return counter_data["count"]


# --- WELCOME & INVITE TRACKER EVENTS ---

@bot.event
async def on_member_join(member: discord.Member):
    welcome_channel = bot.get_channel(WELCOME_CHANNEL_ID)
    if not welcome_channel:
        return

    guild = member.guild
    inviter = None

    try:
        new_invites = await guild.invites()
        cached_invites = invites_cache.get(guild.id, {})

        for invite in new_invites:
            if invite.code in cached_invites and invite.uses > cached_invites[invite.code]:
                inviter = invite.inviter
                break
        
        invites_cache[guild.id] = {inv.code: inv.uses for inv in new_invites}
    except discord.Forbidden:
        pass

    inviter_text = inviter.mention if inviter else "**Unknown / Vanity URL**"

    welcome_embed = discord.Embed(
        title=f"🎉 Welcome to {guild.name}!",
        description=(
            f"Welcome {member.mention}! We're thrilled to have you here.\n\n"
            f"🤝 **Invited by:** {inviter_text}\n"
            f"👥 **Member Count:** #{len(guild.members)}"
        ),
        color=discord.Color.from_rgb(255, 182, 193),
        timestamp=discord.utils.utcnow()
    )
    welcome_embed.set_thumbnail(url=member.display_avatar.url)
    welcome_embed.set_footer(text=f"ID: {member.id}")

    await welcome_channel.send(content=f"Welcome {member.mention}!", embed=welcome_embed)


@bot.event
async def on_invite_create(invite: discord.Invite):
    if invite.guild.id in invites_cache:
        invites_cache[invite.guild.id][invite.code] = invite.uses

@bot.event
async def on_invite_delete(invite: discord.Invite):
    if invite.guild.id in invites_cache:
        invites_cache[invite.guild.id].pop(invite.code, None)


# --- LOGGING FUNCTIONS ---

async def send_staff_initial_log(sender: discord.User, target: discord.User, message: str, dm_sent: bool, conf_num: int):
    staff_channel = bot.get_channel(STAFF_CHANNEL_ID)
    if not staff_channel:
        return

    log_color = discord.Color.from_rgb(138, 43, 226) if dm_sent else discord.Color.from_rgb(178, 34, 34)
    status_badge = "🟢 `DELIVERED`" if dm_sent else "🔴 `DM FAILED`"

    staff_embed = discord.Embed(
        title=f"🔒 Staff Audit Log | Confession (#{conf_num})",
        color=log_color,
        timestamp=discord.utils.utcnow()
    )
    staff_embed.add_field(name="👤 Sender", value=f"{sender.mention}\n`@{sender.name}`", inline=True)
    staff_embed.add_field(name="🎯 Recipient", value=f"{target.mention}\n`@{target.name}`", inline=True)
    staff_embed.add_field(name="📡 Status", value=status_badge, inline=True)
    staff_embed.add_field(name="💬 Message Content", value=f"```fix\n{message}\n```", inline=False)
    staff_embed.set_thumbnail(url=sender.display_avatar.url)

    await staff_channel.send(embed=staff_embed)


async def send_public_log(message: str, conf_num: int):
    public_channel = bot.get_channel(PUBLIC_CHANNEL_ID)
    if not public_channel:
        return

    public_embed = discord.Embed(
        title=f"💌 Confession (#{conf_num})",
        description=f"```\n{message}\n```",
        color=discord.Color.from_rgb(255, 105, 180),
        timestamp=discord.utils.utcnow()
    )
    public_embed.set_footer(text="Anonymous Confession • Use /confess to submit yours!")

    await public_channel.send(embed=public_embed)


async def send_reply_log(sender: discord.User, target: discord.User, message: str, dm_sent: bool):
    replies_channel = bot.get_channel(STAFF_REPLIES_CHANNEL_ID)
    if not replies_channel:
        return

    status_icon = "🟢" if dm_sent else "🔴"
    reply_embed = discord.Embed(
        description=f"{status_icon} **{sender.display_name}** (`@{sender.name}`) replied to **{target.display_name}** (`@{target.name}`):\n```\n{message}\n```",
        color=discord.Color.from_rgb(147, 112, 219) if dm_sent else discord.Color.from_rgb(220, 20, 60),
        timestamp=discord.utils.utcnow()
    )
    reply_embed.set_author(name=sender.display_name, icon_url=sender.display_avatar.url)

    await replies_channel.send(embed=reply_embed)


# --- INTERACTIVE DM BUTTONS & MODALS ---

async def deliver_anonymous_message(sender: discord.User, target_partner_id: str, message_text: str):
    try:
        partner_user = await bot.fetch_user(int(target_partner_id))
    except discord.NotFound:
        return False, "⚠️ Could not find the recipient."

    forward_embed = discord.Embed(
        title="💬 You Received an Anonymous Reply!",
        description=f"```\n{message_text}\n```",
        color=discord.Color.from_rgb(255, 182, 193)
    )
    forward_embed.set_footer(text="💡 Click 'Reply' below or send a message directly to respond back!")

    dm_sent = False
    try:
        view = DMReplyView(partner_id=str(sender.id))
        sent_msg = await partner_user.send(embed=forward_embed, view=view)
        dm_sent = True

        message_map[str(sent_msg.id)] = str(sender.id)
        save_data(message_map, MSG_MAP_FILE)

        user_last_thread[str(sender.id)] = str(partner_user.id)
        user_last_thread[str(partner_user.id)] = str(sender.id)
        save_data(user_last_thread, USER_ACTIVE_FILE)

    except discord.Forbidden:
        pass

    await send_reply_log(sender=sender, target=partner_user, message=message_text, dm_sent=dm_sent)
    return dm_sent, "✨ *Your anonymous reply has been delivered!*" if dm_sent else "⚠️ Message failed to deliver (recipient's DMs are closed)."


class DirectReplyModal(discord.ui.Modal, title="💬 Send Anonymous Reply"):
    reply_input = discord.ui.TextInput(
        label="Your Response",
        style=discord.TextStyle.paragraph,
        placeholder="Type your secret reply here...",
        required=True,
        max_length=1000,
    )

    def __init__(self, target_partner_id: str):
        super().__init__()
        self.target_partner_id = target_partner_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        dm_sent, status_msg = await deliver_anonymous_message(
            sender=interaction.user,
            target_partner_id=self.target_partner_id,
            message_text=self.reply_input.value
        )
        await interaction.followup.send(status_msg, ephemeral=True)


class DMReplyView(discord.ui.View):
    def __init__(self, partner_id: str):
        super().__init__(timeout=None)
        self.partner_id = partner_id

    @discord.ui.button(label="Reply Anonymously", style=discord.ButtonStyle.secondary, emoji="💬")
    async def reply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DirectReplyModal(target_partner_id=self.partner_id))


# --- AUTOMATED CHAT LISTENER ---

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is not None:
        await bot.process_commands(message)
        return

    sender = message.author
    sender_id_str = str(sender.id)
    target_partner_id = None

    if message.reference and message.reference.message_id:
        referenced_msg_id = str(message.reference.message_id)
        if referenced_msg_id in message_map:
            target_partner_id = message_map[referenced_msg_id]

    if not target_partner_id:
        if sender_id_str in user_last_thread:
            target_partner_id = user_last_thread[sender_id_str]

    if target_partner_id:
        _, status_msg = await deliver_anonymous_message(
            sender=sender,
            target_partner_id=target_partner_id,
            message_text=message.content
        )
        await message.channel.send(status_msg)
    else:
        await message.channel.send("💡 You don't have an active confession session. Use `/confess` in a server to start one!")

    await bot.process_commands(message)


# --- COMMANDS ---

class InitialConfessionModal(discord.ui.Modal, title="💌 Send Anonymous Confession"):
    message_input = discord.ui.TextInput(
        label="Your Secret Message",
        style=discord.TextStyle.paragraph,
        placeholder="Write something sweet, funny, or mysterious...",
        required=True,
        max_length=1000,
    )

    def __init__(self, target_user: discord.User):
        super().__init__()
        self.target_user = target_user

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        confession_text = self.message_input.value
        sender = interaction.user
        conf_num = get_next_confession_number()

        recipient_embed = discord.Embed(
            title=f"💌 You Received a Secret Confession! (#{conf_num})",
            description=f"```\n{confession_text}\n```",
            color=discord.Color.from_rgb(255, 182, 193)
        )
        recipient_embed.set_footer(text="💡 Click 'Reply' below or send a message directly to respond back!")

        dm_sent = False
        try:
            view = DMReplyView(partner_id=str(sender.id))
            sent_msg = await self.target_user.send(embed=recipient_embed, view=view)
            dm_sent = True

            message_map[str(sent_msg.id)] = str(sender.id)
            save_data(message_map, MSG_MAP_FILE)

            user_last_thread[str(sender.id)] = str(self.target_user.id)
            user_last_thread[str(self.target_user.id)] = str(sender.id)
            save_data(user_last_thread, USER_ACTIVE_FILE)

            try:
                await interaction.followup.send(f"✨ Confession (#{conf_num}) delivered!", ephemeral=True)
            except Exception as e:
                print(f"Followup network warning (message was sent): {e}")

        except discord.Forbidden:
            try:
                await interaction.followup.send("⚠️ Could not DM the user (their DMs are closed).", ephemeral=True)
            except Exception:
                pass

        await send_staff_initial_log(sender=sender, target=self.target_user, message=confession_text, dm_sent=dm_sent, conf_num=conf_num)
        await send_public_log(message=confession_text, conf_num=conf_num)


class MemberSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="✨ Choose someone special to message...",
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        selected_user = self.values[0]
        await interaction.response.send_modal(InitialConfessionModal(target_user=selected_user))


class MemberSelectView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(MemberSelect())


@bot.tree.command(name="confess", description="Send a stylish anonymous confession to a member!")
async def confess(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤫 Anonymous Confession System",
        description="Select a member from the dropdown below to send them a private message. Your identity will **never** be shown to them!",
        color=discord.Color.from_rgb(255, 105, 180)
    )
    await interaction.response.send_message(
        embed=embed,
        view=MemberSelectView(),
        ephemeral=True
    )


@bot.tree.command(name="roll", description="Roll for Gakuran Styles! (Up to 100 rolls)")
@app_commands.describe(times="How many times to roll (1 - 100)")
async def roll(interaction: discord.Interaction, times: int = 1):
    if times < 1:
        times = 1
    elif times > 100:
        times = 100

    results = random.choices(STYLE_NAMES, weights=STYLE_WEIGHTS, k=times)
    counts = Counter(results)

    got_legendary_or_above = False
    best_color = discord.Color.light_gray()
    tier_priority = {"???": 5, "Legendary": 4, "Epic": 3, "Uncommon": 2, "Common": 1}
    highest_priority = 0

    results_formatted = []

    for style, count in counts.items():
        tier = STYLE_TIERS[style]
        chance = STYLE_CHANCES[style]
        
        if tier in LEGENDARY_OR_ABOVE:
            got_legendary_or_above = True

        if tier_priority[tier] > highest_priority:
            highest_priority = tier_priority[tier]
            best_color = STYLE_COLORS[style]

        count_str = f" x{count}" if times > 1 else ""
        results_formatted.append(f"• **{style}** `{tier}` ({chance}%){count_str}")

    embed = discord.Embed(
        title="🎲 Gakuran Style Roll Results",
        description=f"Rolled **{times}x** for {interaction.user.mention}\n\n" + "\n".join(results_formatted),
        color=best_color,
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.set_footer(text="Gakuran Roll System", icon_url=bot.user.display_avatar.url)

    content_ping = f"🚨 **LEGENDARY ROLL!** {interaction.user.mention}" if got_legendary_or_above else None
    allowed_mentions = discord.AllowedMentions(users=got_legendary_or_above)

    await interaction.response.send_message(
        content=content_ping, 
        embed=embed, 
        allowed_mentions=allowed_mentions
    )


@bot.event
async def on_ready():
    await bot.tree.sync()
    
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            invites_cache[guild.id] = {inv.code: inv.uses for inv in invites}
        except discord.Forbidden:
            print(f"Lacking 'Manage Server' permissions to track invites in {guild.name}")

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("No DISCORD_TOKEN found in environment variables!")
    bot.run(TOKEN)