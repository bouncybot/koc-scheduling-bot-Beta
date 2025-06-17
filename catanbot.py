import os
import json	
from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord import app_commands
from google.oauth2 import service_account
from googleapiclient.discovery import build
import re
from datetime import datetime, timedelta, timezone
from collections import defaultdict

load_dotenv()  # loads the .env file

TOKEN = os.getenv("DISCORD_BOT_TOKEN")

if TOKEN is None:
    raise ValueError("DISCORD_BOT_TOKEN not found in environment variables")


service_account_info = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))
SPREADSHEET_ID = '1ew495Ktf6LIA5-Hd7zG6ISJlHUsBbp8eKdZteQeoMH4'
RANGE_NAME = "'Formulierreacties 1'!A:E"

# Google Sheets setup
credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
)		
sheet_service = build('sheets', 'v4', credentials=credentials).spreadsheets()

# Discord bot setup
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def group_time_slots(slots):
    day_hours = {}
    for slot in slots.split(','):
        slot = slot.strip()
        match = re.match(r'(\w+) (\d{2}):\d{2} UTC', slot)
        if not match:
            continue
        day = match.group(1)
        hour = int(match.group(2))
        day_hours.setdefault(day, []).append(hour)

    grouped = []
    for day, hours in day_hours.items():
        hours.sort()
        start = prev = hours[0]
        for h in hours[1:]:
            if h == prev + 1:
                prev = h
            else:
                grouped.append(f"{day} {start:02d}:00 UTC - {prev+1:02d}:00 UTC" if start != prev else f"{day} {start:02d}:00 UTC")
                start = prev = h
        grouped.append(f"{day} {start:02d}:00 UTC - {prev+1:02d}:00 UTC" if start != prev else f"{day} {start:02d}:00 UTC")
    return '\n'.join(grouped)

def intersect_availability(rows):
    sets_of_slots = [set(slot.strip() for slot in row.split(',')) for row in rows]
    common_slots = set.intersection(*sets_of_slots) if sets_of_slots else set()
    if not common_slots:
        return "❌ No common availability found."
    def sort_key(slot):
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        day, time, *_ = slot.split()
        hour = int(time.split(':')[0])
        return (days.index(day), hour)
    common_sorted = sorted(common_slots, key=sort_key)
    return group_time_slots(', '.join(common_sorted))

def suggest_slots_with_3_players(rows, player_names):
    slot_to_players = defaultdict(list)
    for player, row in zip(player_names, rows):
        for slot in row.split(','):
            slot = slot.strip()
            if slot:
                slot_to_players[slot].append(player)

    suggestions = []
    for slot, players in slot_to_players.items():
        if len(players) == 3:
            suggestions.append(f"{slot} ({', '.join(players)})")

    def sort_key(s):
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        match = re.match(r"(\w+) (\d{2}):\d{2} UTC", s)
        if not match:
            return (99, 99)
        day, hour = match.group(1), int(match.group(2))
        return (days.index(day), hour)

    return "\n".join(sorted(suggestions, key=sort_key)) if suggestions else "❌ No good matches for 3 players either."

class ManualScheduleModal(discord.ui.Modal, title="Manual Match Scheduling"):
    date_input = discord.ui.TextInput(
        label="Date (YYYY-MM-DD)", placeholder="2025-06-15", required=True
    )
    time_input = discord.ui.TextInput(
        label="Time (HH:MM, 24h UTC)", placeholder="18:00", required=True
    )

    def __init__(self, table, round, players):
        super().__init__()
        self.table = table
        self.round = round
        self.players = players

    async def on_submit(self, interaction: discord.Interaction):
        try:
            date = self.date_input.value.strip()
            time = self.time_input.value.strip()
            dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
            dt = dt.replace(tzinfo=timezone.utc)

            prefix = "PL" if int(self.table) <= 6 else "PCL"
            player_str = ', '.join(self.players)

            event = await interaction.guild.create_scheduled_event(
                name=f"{prefix} - Table {self.table}, Round {self.round} - {player_str}",
                start_time=dt,
                end_time=dt + timedelta(hours=2),
                description=f"Manually scheduled by players for Table {self.table}, Round {self.round}.",
                location="Online",
                entity_type=discord.EntityType.external,
                privacy_level=discord.PrivacyLevel.guild_only
            )

            await interaction.response.send_message(
                f"📅 Manual match scheduled for **{date} {time} UTC**!\nEvent created: {event.name}",
                ephemeral=False
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error creating event: {e}", ephemeral=True)

class ManualScheduleButton(discord.ui.Button):
    def __init__(self, table, round, players):
        super().__init__(label="📆 Manually Schedule Match", style=discord.ButtonStyle.primary)
        self.table = table
        self.round = round
        self.players = players

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ManualScheduleModal(self.table, self.round, self.players))

class AvailabilitySelect(discord.ui.Select):
    def __init__(self, options, table, round, players):
        super().__init__(
            placeholder="🕒 Choose a time slot for the match...",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label=opt) for opt in options]
        )
        self.table = table
        self.round = round
        self.players = players

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        now_utc = discord.utils.utcnow()
        day_map = {
            "Monday": 0, "Tuesday": 1, "Wednesday": 2,
            "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6
        }

        match = re.match(r"(\w+) (\d{2}):00 UTC", chosen)
        if not match:
            await interaction.response.send_message("Invalid time format.", ephemeral=True)
            return

        weekday_str, hour = match.group(1), int(match.group(2))
        weekday = day_map[weekday_str]
        days_ahead = (weekday - now_utc.weekday() + 7) % 7
        match_time = (now_utc + timedelta(days=days_ahead)).replace(hour=hour, minute=0, second=0, microsecond=0)

        # ✅ FIX: Als het berekende tijdstip in het verleden ligt, schuif het een week vooruit
        if match_time < now_utc:
            match_time += timedelta(days=7)

        prefix = "PL" if int(self.table) <= 6 else "PCL"
        player_str = ', '.join(self.players)

        try:
            scheduled_event = await interaction.guild.create_scheduled_event(
                name=f"{prefix} - Table {self.table}, Round {self.round} - {player_str}",
                start_time=match_time,
                end_time=match_time + timedelta(hours=2),
                description=f"Scheduled by players for Table {self.table}, Round {self.round}.",
                location="Online",
                entity_type=discord.EntityType.external,
                privacy_level=discord.PrivacyLevel.guild_only
            )

            await interaction.response.send_message(
                f"✅ Match scheduled for **{chosen}**!\nEvent created: {scheduled_event.name}",
                ephemeral=False
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Could not create event: {e}", ephemeral=True)


class AvailabilityView(discord.ui.View):
    def __init__(self, options, table, round, players, show_manual_button=False):
        super().__init__(timeout=None)
        if options:
            self.add_item(AvailabilitySelect(options, table, round, players))
        if show_manual_button:
            self.add_item(ManualScheduleButton(table, round, players))

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    await bot.tree.sync()
    print("✅ Slash commands synced!")

@bot.tree.command(name="availability", description="Show availability and schedule a match")
@app_commands.describe(table="Table number (e.g. 1)", round="Round number (e.g. 1, 2, semifinal, final)")
async def availability(interaction: discord.Interaction, table: str, round: str):
    await interaction.response.defer()

    try:
        result = sheet_service.values().get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME).execute()
        values = result.get("values", [])
    except Exception as e:
        await interaction.followup.send(f"❌ Could not fetch data: {e}")
        return

    table_number = re.findall(r"\d+", table)
    round_number = re.findall(r"\d+", round)

    if not table_number or not round_number:
        await interaction.followup.send("⚠️ Please enter valid table and round numbers.")
        return

    table_number = table_number[0]
    round_number = round_number[0]

    filtered = []
    for row in values[1:]:
        if len(row) >= 5:
            row_round = re.findall(r"\d+", row[2])
            row_table = re.findall(r"\d+", row[3])
            if row_table and row_round and row_table[0] == table_number and row_round[0] == round_number:
                filtered.append(row)

    if not filtered:
        await interaction.followup.send("❌ No availability found for this table and round.")
        return

    embed = discord.Embed(
        title=f"📅 Availability: Table {table_number}, Round {round_number}",
        color=discord.Color.green(),
        description="Below are the time slots provided by each player."
    )

    availability_rows = []
    all_slots_set = set()
    player_names = []

    for row in filtered:
        player = row[1]
        player_names.append(player)
        raw_slots = row[4]
        availability_rows.append(raw_slots)
        grouped = group_time_slots(raw_slots)
        all_slots_set.update(slot.strip() for slot in raw_slots.split(','))
        embed.add_field(name=f"👤 {player}", value=grouped, inline=False)

    show_manual = False
    common = intersect_availability(availability_rows)
    if "❌" in common:
        suggestion = suggest_slots_with_3_players(availability_rows, player_names)
        embed.add_field(name="❌ No match for all 4 players", value="Below are options where 3 players are available.", inline=False)
        embed.add_field(name="🤝 Partial Availability", value=suggestion, inline=False)
        dropdown_options = []
        show_manual = True
    else:
        embed.add_field(name="✅ Common Availability", value=common, inline=False)
        dropdown_options = sorted(set(slot.strip() for slot in common.split('\n') if "UTC" in slot))

    view = AvailabilityView(dropdown_options, table_number, round_number, player_names, show_manual_button=show_manual)
    await interaction.followup.send(embed=embed, view=view)

bot.run(TOKEN)