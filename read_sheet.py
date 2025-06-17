import os
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Configuratie
SERVICE_ACCOUNT_FILE = 'koc-scheduling-edd380626bae.json'
SPREADSHEET_ID = '1ew495Ktf6LIA5-Hd7zG6ISJlHUsBbp8eKdZteQeoMH4'
RANGE_NAME = "'Formulierreacties 1'!A:E"

# Credentials aanmaken
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'],
)

# Service object aanmaken
service = build('sheets', 'v4', credentials=credentials)

# Data ophalen
sheet = service.spreadsheets()
result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME).execute()
values = result.get('values', [])

if not values:
    print('No data found.')
else:
    data = values[1:]  # header overslaan

    availability = {}

    for row in data:
        _, discord_user, table, round_, time_slots_str = row

        time_slots = [slot.strip() for slot in time_slots_str.split(',')]

        if table not in availability:
            availability[table] = {}
        if round_ not in availability[table]:
            availability[table][round_] = {}

        for slot in time_slots:
            if slot not in availability[table][round_]:
                availability[table][round_][slot] = []
            availability[table][round_][slot].append(discord_user)

    # Print overzicht
    for table, rounds in availability.items():
        print(f"\nTable: {table}")
        for round_, slots in rounds.items():
            print(f"  Round: {round_}")
            for slot, players in slots.items():
                print(f"    Time: {slot} -> Players: {', '.join(players)}")

    # Discord bericht maken
    message = "Catan Match Scheduling Availability:\n"

    for table, rounds in availability.items():
        message += f"\n**Table: {table}**\n"
        for round_, slots in rounds.items():
            message += f"  __Round: {round_}__\n"
            for slot, players in slots.items():
                players_list = ', '.join(players)
                message += f"    - {slot}: {players_list}\n"

    print("\n--- Discord Message Preview ---\n")
    print(message)
