import os
import json
import random
import threading
from datetime import datetime, timezone, timedelta
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

# ==========================================
# 1. KEEP-ALIVE WEB SERVER (Fixes UptimeRobot 501 Error)
# ==========================================
class WebServerHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        """Responds to UptimeRobot HEAD requests with 200 OK."""
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        """Responds to UptimeRobot GET requests or web browsers."""
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Bot is online and running!")

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), WebServerHandler)
    server.serve_forever()

# Run the web server in a background thread
threading.Thread(target=start_web_server, daemon=True).start()


# ==========================================
# 2. DISCORD BOT & INTENTS SETUP
# ==========================================
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.dm_messages = True
intents.members = True       # Required to track member joins and moderations
intents.invites = True       # Required to track invites

# Disable default help command
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Channel IDs
WELCOME_CHANNEL_ID = 1544178863280758855       # Join / Welcome Channel ID
STAFF_CHANNEL_ID = 1543969779591815333          # Unused initial channel
PUBLIC_CHANNEL_ID = 1547265722525290536         # Public Anonymous Log (Visible to ALL, Read-Only)
STAFF_REPLIES_CHANNEL_ID = 1544215871885541386  # All Staff Logs & Replies (Admins Only, Read-Only)
DAILY_QUOTE_CHANNEL_ID = 1547444666700537956    # 24-Hour Quote Target Channel
MOD_LOG_CHANNEL_ID = 1544478399198928990        # Moderation Log Channel ID (Admins Only, Read-Only)

# Role IDs for Ticket Permissions & Divisions
TICKET_STAFF_ROLE_IDS = [
    1544005525530878024,
    1543968513952321749,
    1544220934360272916,
    1544221064773505034
]

DIVISION_ROLES = {
    "1st Division": 1544221064773505034,
    "2nd Division": 1544221064773505034,
    "3rd Division": 1544691913431584779,
    "4th Division": 1544691956590841876,
    "5th Division": 1544691994968981564
}

TAIIN_ROLE_ID = 1544321376675168409
TRYOUT_ROLE_ID = 1544208971848745090

MSG_MAP_FILE = "message_map.json"
USER_ACTIVE_FILE = "user_active_threads.json"
COUNTER_FILE = "counter.json"
WARNINGS_FILE = "warnings.json"
QUOTE_STATE_FILE = "quote_state.json"

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
    if file_path == COUNTER_FILE:
        return {"count": 0}
    return {}

def save_data(data, file_path):
    with open(file_path, "w") as f:
        json.dump(data, f, indent=4)

message_map = load_data(MSG_MAP_FILE)
user_last_thread = load_data(USER_ACTIVE_FILE)
counter_data = load_data(COUNTER_FILE)
warnings_data = load_data(WARNINGS_FILE)
quote_state = load_data(QUOTE_STATE_FILE)


def get_next_confession_number():
    counter_data["count"] += 1
    save_data(counter_data, COUNTER_FILE)
    return counter_data["count"]


# --- HELPER PERMISSION CHECK ---

def is_staff(member: discord.Member) -> bool:
    """Check if the member has any of the ticket staff roles or admin permissions."""
    if member.guild_permissions.administrator:
        return True
    return any(role.id in TICKET_STAFF_ROLE_IDS for role in member.roles)


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

async def send_staff_initial_log(sender: discord.User, target: discord.User, message: str, dm_sent: bool, conf_num: int, is_locked: bool = False):
    replies_channel = bot.get_channel(STAFF_REPLIES_CHANNEL_ID)
    if not replies_channel:
        return

    log_color = discord.Color.from_rgb(138, 43, 226) if dm_sent else discord.Color.from_rgb(178, 34, 34)
    status_badge = "🟢 `DELIVERED`" if dm_sent else "🔴 `DM FAILED`"
    lock_status = "🔒 `LOCKED (No Replies)`" if is_locked else "🔓 `UNLOCKED (Replies Allowed)`"

    staff_embed = discord.Embed(
        title=f"🔒 Staff Audit Log | Confession (#{conf_num})",
        color=log_color,
        timestamp=discord.utils.utcnow()
    )
    staff_embed.add_field(name="👤 Sender", value=f"{sender.mention}\n`@{sender.name}`", inline=True)
    staff_embed.add_field(name="🎯 Recipient", value=f"{target.mention}\n`@{target.name}`", inline=True)
    staff_embed.add_field(name="📡 Status", value=status_badge, inline=True)
    staff_embed.add_field(name="🔒 Thread Mode", value=lock_status, inline=False)
    staff_embed.add_field(name="💬 Message Content", value=f"```fix\n{message}\n```", inline=False)
    staff_embed.set_thumbnail(url=sender.display_avatar.url)

    await replies_channel.send(embed=staff_embed)


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


# --- QUOTE HELPER & PERSISTENT 24-HOUR AUTOMATION TASK ---

async def fetch_quote():
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get("https://zenquotes.io/api/quotes") as response:
                if response.status == 200:
                    data = await response.json()
                    selected = random.choice(data)
                    return f'"{selected["q"]}"\n— {selected["a"]}'
                return "Could not fetch a quote right now."
        except Exception as e:
            print(f"Error fetching quote: {e}")
            return "Failed to connect to the quote service."


@tasks.loop(hours=24)
async def auto_post_quote():
    channel = bot.get_channel(DAILY_QUOTE_CHANNEL_ID)
    if channel:
        quote_text = await fetch_quote()
        await channel.send(quote_text)
        
        quote_state["last_posted"] = datetime.now(timezone.utc).isoformat()
        save_data(quote_state, QUOTE_STATE_FILE)


@auto_post_quote.before_loop
async def before_auto_post_quote():
    await bot.wait_until_ready()

    last_posted_str = quote_state.get("last_posted")
    if last_posted_str:
        try:
            last_posted = datetime.fromisoformat(last_posted_str)
            now = datetime.now(timezone.utc)
            elapsed = (now - last_posted).total_seconds()
            twenty_four_hours = 24 * 3600

            if elapsed < twenty_four_hours:
                remaining_seconds = twenty_four_hours - elapsed
                print(f"Quote state restored. Next automated quote in {int(remaining_seconds / 3600)}h {int((remaining_seconds % 3600) / 60)}m.")
                await discord.utils.sleep_until(now + timedelta(seconds=remaining_seconds))
        except Exception as e:
            print(f"Error restoring quote state timestamp: {e}")


# ==========================================
# 3. TICKET SYSTEM IMPLEMENTATION
# ==========================================

class DivisionSelect(discord.ui.Select):
    def __init__(self, ticket_owner: discord.Member):
        options = [
            discord.SelectOption(label="1st Division", description="Assign 1st Division"),
            discord.SelectOption(label="2nd Division", description="Assign 2nd Division"),
            discord.SelectOption(label="3rd Division", description="Assign 3rd Division"),
            discord.SelectOption(label="4th Division", description="Assign 4th Division"),
            discord.SelectOption(label="5th Division", description="Assign 5th Division"),
        ]
        super().__init__(placeholder="Choose a Division for the recruit...", min_values=1, max_values=1, options=options)
        self.ticket_owner = ticket_owner

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Only authorized staff can assign divisions.", ephemeral=True)
            return

        division_name = self.values[0]
        div_role_id = DIVISION_ROLES.get(division_name)

        guild = interaction.guild
        division_role = guild.get_role(div_role_id)
        taiin_role = guild.get_role(TAIIN_ROLE_ID)
        tryout_role = guild.get_role(TRYOUT_ROLE_ID)

        # Process role updates
        if division_role:
            await self.ticket_owner.add_roles(division_role, reason=f"Tryout Passed ({division_name})")
        if taiin_role:
            await self.ticket_owner.add_roles(taiin_role, reason="Tryout Passed - Added Taiin Role")
        if tryout_role and tryout_role in self.ticket_owner.roles:
            await self.ticket_owner.remove_roles(tryout_role, reason="Tryout Passed - Removed Tryout Role")

        # Send confirmation message
        await interaction.response.send_message(
            f"✅ **Tryout Completed!**\nassigned {self.ticket_owner.mention} to **{division_name}** and granted the **Taiin** role.",
            ephemeral=False
        )

        # Log tryout to Staff Logs
        log_channel = bot.get_channel(STAFF_REPLIES_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(
                title="⚔️ Tryout Completed Log",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            log_embed.add_field(name="🛡️ Evaluator (Staff)", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="👤 Recruit", value=f"{self.ticket_owner.mention} (`{self.ticket_owner.id}`)", inline=True)
            log_embed.add_field(name="🚩 Division Assigned", value=division_name, inline=False)
            log_embed.set_thumbnail(url=self.ticket_owner.display_avatar.url)
            await log_channel.send(embed=log_embed)

        # Disable selection after use
        self.disabled = True
        await interaction.message.edit(view=self.view)


class DivisionSelectView(discord.ui.View):
    def __init__(self, ticket_owner: discord.Member):
        super().__init__(timeout=None)
        self.add_item(DivisionSelect(ticket_owner=ticket_owner))


class TicketControlView(discord.ui.View):
    def __init__(self, ticket_owner: discord.Member):
        super().__init__(timeout=None)
        self.ticket_owner = ticket_owner

    @discord.ui.button(label="Done Tryout", style=discord.ButtonStyle.success, emoji="✅", custom_id="ticket_done_tryout")
    async def done_tryout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Only authorized staff can complete tryouts.", ephemeral=True)
            return

        view = DivisionSelectView(ticket_owner=self.ticket_owner)
        await interaction.response.send_message("Please select a Division for this recruit:", view=view, ephemeral=True)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Only staff members can close tickets.", ephemeral=True)
            return

        await interaction.response.send_message("🔒 Closing ticket in 5 seconds...")
        await discord.utils.sleep_until(discord.utils.utcnow() + timedelta(seconds=5))
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user.name}")


class TicketFormModal(discord.ui.Modal, title="📋 Tryout Application Form"):
    q1 = discord.ui.TextInput(label="Roblox & Gakuran Name", placeholder="e.g. Roblox/Wang Ling", required=True)
    q2 = discord.ui.TextInput(label="Region / Country", placeholder="e.g. Asia/Philippines", required=True)
    q3 = discord.ui.TextInput(label="Age", placeholder="e.g. 18", required=True, max_length=3)
    q4 = discord.ui.TextInput(label="Fighting Style", placeholder="e.g. Kure/Hakari", required=True)
    q5 = discord.ui.TextInput(label="Who invited you here?", placeholder="Discord Name / Gakuran Name", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        applicant = interaction.user

        # Create private text channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            applicant: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        for role_id in TICKET_STAFF_ROLE_IDS:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel_name = f"ticket-{applicant.name}".lower()[:32]
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            reason=f"Ticket created for {applicant.name}"
        )

        # Construct staff pings
        ping_mentions = " ".join([f"<@&{r_id}>" for r_id in TICKET_STAFF_ROLE_IDS])

        # Application Embed
        embed = discord.Embed(
            title=f"🎫 Ticket Application — {applicant.display_name}",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="1️⃣ Roblox / Gakuran Name", value=self.q1.value, inline=False)
        embed.add_field(name="2️⃣ Region / Country", value=self.q2.value, inline=False)
        embed.add_field(name="3️⃣ Age", value=self.q3.value, inline=False)
        embed.add_field(name="4️⃣ Fighting Style", value=self.q4.value, inline=False)
        embed.add_field(name="5️⃣ Invited By", value=self.q5.value, inline=False)
        embed.set_thumbnail(url=applicant.display_avatar.url)

        view = TicketControlView(ticket_owner=applicant)
        await ticket_channel.send(content=f"{applicant.mention} {ping_mentions}", embed=embed, view=view)

        await interaction.followup.send(f"✅ Ticket created! Please head over to {ticket_channel.mention}", ephemeral=True)


class SpawnTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="spawn_ticket_create")
    async def create_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketFormModal())


@bot.tree.command(name="spawnticket", description="Spawn the ticket creation message in this channel")
@app_commands.checks.has_permissions(administrator=True)
async def spawnticket(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎫 Tryout & Support Ticket System",
        description="Click the **Create Ticket** button below to open a ticket and apply for tryouts!",
        color=discord.Color.gold()
    )
    embed.set_footer(text="Click below to get started!")
    await interaction.channel.send(embed=embed, view=SpawnTicketView())
    await interaction.response.send_message("✅ Ticket panel successfully spawned!", ephemeral=True)


@spawnticket.error
async def spawnticket_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You lack permissions (Administrator) to use `/spawnticket`.", ephemeral=True)


# ==========================================
# 4. COMMANDS & MODERATION
# ==========================================

@bot.command(name="quote")
async def quote_prefix(ctx):
    quote = await fetch_quote()
    await ctx.send(quote)


@bot.tree.command(name="quote", description="Get a random inspirational quote")
async def quote_slash(interaction: discord.Interaction):
    quote = await fetch_quote()
    await interaction.response.send_message(quote)


@bot.tree.command(name="warn", description="Warn a member and log it")
@app_commands.describe(member="The member to warn", reason="Reason for the warning")
@app_commands.checks.has_permissions(manage_messages=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.top_role >= interaction.user.top_role and interaction.guild.owner != interaction.user:
        await interaction.response.send_message("❌ You cannot warn this member as their role is equal to or higher than yours.", ephemeral=True)
        return

    if member.bot:
        await interaction.response.send_message("❌ You cannot warn a bot.", ephemeral=True)
        return

    user_id = str(member.id)
    if user_id not in warnings_data:
        warnings_data[user_id] = []

    warnings_data[user_id].append({
        "reason": reason,
        "moderator": interaction.user.name,
        "timestamp": str(discord.utils.utcnow())
    })
    save_data(warnings_data, WARNINGS_FILE)

    total_warns = len(warnings_data[user_id])

    warn_dm_embed = discord.Embed(
        title="⚠️ You Have Received a Warning",
        description=f"You received a warning in **{interaction.guild.name}**.\n\n**Reason:** {reason}\n**Total Warnings:** {total_warns}/6",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow()
    )
    
    dm_sent = True
    try:
        await member.send(embed=warn_dm_embed)
    except discord.Forbidden:
        dm_sent = False

    log_channel = bot.get_channel(MOD_LOG_CHANNEL_ID)

    if log_channel:
        log_embed = discord.Embed(
            title=f"⚠️ Member Warned | Warning #{total_warns}",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        log_embed.add_field(name="👤 Target User", value=f"{member.mention} (`{member.id}`)", inline=True)
        log_embed.add_field(name="🛡️ Moderator", value=f"{interaction.user.mention}", inline=True)
        log_embed.add_field(name="📊 Active Warnings", value=f"`{total_warns}/6`", inline=True)
        log_embed.add_field(name="📝 Reason", value=f"```\n{reason}\n```", inline=False)
        log_embed.add_field(name="📬 DM Status", value="🟢 `Delivered`" if dm_sent else "🔴 `Failed (DMs Closed)`", inline=False)
        log_embed.set_thumbnail(url=member.display_avatar.url)
        await log_channel.send(embed=log_embed)

    escalation_text = ""
    if total_warns >= 6:
        try:
            await member.ban(reason=f"Reached 6 warnings. Last reason: {reason}")
            escalation_text = "\n⛔ **Member was automatically BANNED for reaching 6 warnings.**"
        except discord.Forbidden:
            escalation_text = "\n⚠️ *Failed to ban member due to missing permissions.*"
    elif total_warns == 3:
        try:
            await member.timeout(timedelta(hours=1), reason=f"Reached 3 warnings. Last reason: {reason}")
            escalation_text = "\n🔇 **Member was automatically MUTED for 1 hour (3 warnings reached).**"
        except discord.Forbidden:
            escalation_text = "\n⚠️ *Failed to mute member due to missing permissions.*"

    await interaction.response.send_message(
        f"✅ **{member.display_name}** has been warned ({total_warns}/6 warnings).{escalation_text}",
        ephemeral=True
    )


@warn.error
async def warn_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You lack permissions to use `/warn`.", ephemeral=True)


@bot.tree.command(name="warnings", description="Check warnings for a member")
@app_commands.describe(member="The member to check")
async def warnings(interaction: discord.Interaction, member: discord.Member):
    user_id = str(member.id)
    user_warns = warnings_data.get(user_id, [])

    if not user_warns:
        await interaction.response.send_message(f"✅ **{member.display_name}** has clean records (0 warnings).", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"📋 Warnings for {member.display_name} ({len(user_warns)}/6)",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    
    for idx, warn_entry in enumerate(user_warns, start=1):
        embed.add_field(
            name=f"Warning #{idx}",
            value=f"**Reason:** {warn_entry['reason']}\n**Moderator:** {warn_entry['moderator']}",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="clearwarn", description="Clear all warnings for a member")
@app_commands.describe(member="The member whose warnings you want to clear")
@app_commands.checks.has_permissions(manage_messages=True)
async def clearwarn(interaction: discord.Interaction, member: discord.Member):
    user_id = str(member.id)

    if user_id not in warnings_data or not warnings_data[user_id]:
        await interaction.response.send_message(f"ℹ️ **{member.display_name}** already has 0 active warnings.", ephemeral=True)
        return

    cleared_count = len(warnings_data[user_id])
    warnings_data[user_id] = []
    save_data(warnings_data, WARNINGS_FILE)

    log_channel = bot.get_channel(MOD_LOG_CHANNEL_ID)
    if log_channel:
        log_embed = discord.Embed(
            title="🧹 Warnings Cleared",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        log_embed.add_field(name="👤 User", value=f"{member.mention} (`{member.id}`)", inline=True)
        log_embed.add_field(name="🛡️ Moderator", value=f"{interaction.user.mention}", inline=True)
        log_embed.add_field(name="🗑️ Cleared Count", value=f"`{cleared_count}` warning(s)", inline=True)
        await log_channel.send(embed=log_embed)

    await interaction.response.send_message(f"🧹 Cleared **{cleared_count}** warning(s) for **{member.display_name}**.", ephemeral=True)


@clearwarn.error
async def clearwarn_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You lack permissions to use `/clearwarn`.", ephemeral=True)


@bot.tree.command(name="mute", description="Mute (timeout) a member")
@app_commands.describe(member="The member to mute", duration_minutes="Mute duration in minutes", reason="Reason for mute")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, member: discord.Member, duration_minutes: int, reason: str = "No reason provided"):
    if member.top_role >= interaction.user.top_role and interaction.guild.owner != interaction.user:
        await interaction.response.send_message("❌ You cannot mute this member.", ephemeral=True)
        return

    try:
        await member.timeout(timedelta(minutes=duration_minutes), reason=reason)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Bot lacks permission to mute this member.", ephemeral=True)
        return

    log_channel = bot.get_channel(MOD_LOG_CHANNEL_ID)
    if log_channel:
        log_embed = discord.Embed(
            title="🔇 Member Muted",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        log_embed.add_field(name="👤 User", value=f"{member.mention}", inline=True)
        log_embed.add_field(name="🛡️ Moderator", value=f"{interaction.user.mention}", inline=True)
        log_embed.add_field(name="⏳ Duration", value=f"{duration_minutes} minutes", inline=True)
        log_embed.add_field(name="📝 Reason", value=f"```\n{reason}\n```", inline=False)
        await log_channel.send(embed=log_embed)

    await interaction.response.send_message(f"🔇 **{member.display_name}** has been muted for {duration_minutes} minutes.", ephemeral=True)


@bot.tree.command(name="unmute", description="Unmute a member")
@app_commands.describe(member="The member to unmute")
@app_commands.checks.has_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, member: discord.Member):
    try:
        await member.timeout(None, reason="Unmuted by moderator")
    except discord.Forbidden:
        await interaction.response.send_message("❌ Bot lacks permission to unmute this member.", ephemeral=True)
        return

    await interaction.response.send_message(f"🔊 **{member.display_name}** has been unmuted.", ephemeral=True)


@bot.tree.command(name="ban", description="Ban a member from the server")
@app_commands.describe(member="The member to ban", reason="Reason for ban")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.top_role >= interaction.user.top_role and interaction.guild.owner != interaction.user:
        await interaction.response.send_message("❌ You cannot ban this member.", ephemeral=True)
        return

    try:
        await member.ban(reason=reason)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Bot lacks permission to ban this member.", ephemeral=True)
        return

    log_channel = bot.get_channel(MOD_LOG_CHANNEL_ID)
    if log_channel:
        log_embed = discord.Embed(
            title="⛔ Member Banned",
            color=discord.Color.dark_red(),
            timestamp=discord.utils.utcnow()
        )
        log_embed.add_field(name="👤 User", value=f"{member.mention} (`{member.id}`)", inline=True)
        log_embed.add_field(name="🛡️ Moderator", value=f"{interaction.user.mention}", inline=True)
        log_embed.add_field(name="📝 Reason", value=f"```\n{reason}\n```", inline=False)
        await log_channel.send(embed=log_embed)

    await interaction.response.send_message(f"⛔ **{member.display_name}** has been banned.", ephemeral=True)


# --- CONFESSIONS MODALS & VIEWS ---

class InitialConfessionModal(discord.ui.Modal):
    message_input = discord.ui.TextInput(
        label="Your Secret Message",
        style=discord.TextStyle.paragraph,
        placeholder="Write something sweet, funny, or mysterious...",
        required=True,
        max_length=1000,
    )

    def __init__(self, target_user: discord.User, allow_replies: bool = True):
        super().__init__(title=f"💌 Confessing to {target_user.display_name[:20]}")
        self.target_user = target_user
        self.allow_replies = allow_replies

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

        if self.allow_replies:
            recipient_embed.set_footer(text="💡 Click 'Reply' below or send a message directly to respond back!")
            view = DMReplyView(partner_id=str(sender.id))
        else:
            recipient_embed.set_footer(text="🔒 The sender disabled replies for this confession.")
            view = None

        dm_sent = False
        try:
            if view:
                sent_msg = await self.target_user.send(embed=recipient_embed, view=view)
            else:
                sent_msg = await self.target_user.send(embed=recipient_embed)

            dm_sent = True

            if self.allow_replies:
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

        await send_staff_initial_log(sender=sender, target=self.target_user, message=confession_text, dm_sent=dm_sent, conf_num=conf_num, is_locked=not self.allow_replies)
        await send_public_log(message=confession_text, conf_num=conf_num)


class MemberSelect(discord.ui.UserSelect):
    def __init__(self, allow_replies: bool = True):
        super().__init__(
            placeholder="✨ Choose someone special to message...",
            min_values=1,
            max_values=1
        )
        self.allow_replies = allow_replies

    async def callback(self, interaction: discord.Interaction):
        selected_user = self.values[0]
        await interaction.response.send_modal(InitialConfessionModal(target_user=selected_user, allow_replies=self.allow_replies))


class MemberSelectView(discord.ui.View):
    def __init__(self, allow_replies: bool = True):
        super().__init__()
        self.add_item(MemberSelect(allow_replies=allow_replies))


@bot.tree.command(name="confess", description="Send a stylish anonymous confession to a member!")
@app_commands.describe(allow_replies="Allow the recipient to reply anonymously back to you?")
async def confess(interaction: discord.Interaction, allow_replies: bool = True):
    status_text = "enabled" if allow_replies else "disabled 🔒"
    embed = discord.Embed(
        title="🤫 Anonymous Confession System",
        description=f"Select a member from the dropdown below to send them a private message.\n\nReplies are currently **{status_text}**. Your identity will **never** be shown to them!",
        color=discord.Color.from_rgb(255, 105, 180)
    )
    await interaction.response.send_message(
        embed=embed,
        view=MemberSelectView(allow_replies=allow_replies),
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


# --- ON_READY EVENT ---

@bot.event
async def on_ready():
    # Register persistent views so buttons remain active across bot restarts
    bot.add_view(SpawnTicketView())
    
    await bot.tree.sync()
    
    if not auto_post_quote.is_running():
        auto_post_quote.start()

    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            invites_cache[guild.id] = {inv.code: inv.uses for inv in invites}
        except discord.Forbidden:
            print(f"Lacking 'Manage Server' permissions to track invites in {guild.name}")

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


# ==========================================
# RUN THE BOT
# ==========================================
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("Error: DISCORD_TOKEN environment variable is missing!")
else:
    bot.run(TOKEN)