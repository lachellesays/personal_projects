import pandas as pd
import requests
from supabase import create_client

# 1. API Configuration
API_KEY = '76680f3e636f49a630da81de5668a282'
FORM_ID = '261153536294155'
ENDPOINT = f"https://api.jotform.com/form/{FORM_ID}/submissions"
SUPABASE_URL = "https://qhuwjffkenhifnhosojq.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFodXdqZmZrZW5oaWZuaG9zb2pxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NjI3MTMyNywiZXhwIjoyMDkxODQ3MzI3fQ.loWDN09y4vP4Oa-0GR_o8LVa0kOraJtYIpLfS1YMOos"

# --- 2. FETCH FROM JOTFORM ---
# Add &limit=1000 to ensure you get all submissions, not just the first 20
ENDPOINT = f"https://api.jotform.com/form/{FORM_ID}/submissions?apiKey={API_KEY}&limit=1000"
response = requests.get(ENDPOINT)
submissions = response.json().get('content', [])

# --- 3. FLATTEN DATA ---
list_of_rows = []
for sub in submissions:
    row = {"submission_id": sub.get('id'), "created_at": sub.get('created_at')}
    answers = sub.get('answers', {})
    for f_id, f_data in answers.items():
        row[f_data.get('text', f"f_{f_id}")] = f_data.get('answer')
    list_of_rows.append(row)

df = pd.DataFrame(list_of_rows)

# --- 4. SELECT & MAP TO SUPABASE ---
# Using the specific indexes you requested
df_final = df.iloc[:, [0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 17, 19, 20]].copy()

# Rename to match your Supabase columns exactly
df_final.columns = [
    "submission_id", "created_at", "handler_name", "handler_number",
    "dog_name", "dog_number", "jump_height", "international_level",
    "speedstakes_level", "saturday_classes", "sunday_classes", 
    "payment_data", "Email", "address", "dog_breed"
]

# --- 5. CLEANING FOR SQL ---
# Ensure submission_id is a number for the BIGINT column
df_final['submission_id'] = pd.to_numeric(df_final['submission_id'])

# Convert everything else to string to avoid "dict" errors in text columns
# (Jotform sometimes sends address/names as nested dictionaries)
for col in df_final.columns:
    if col != 'submission_id':
        df_final[col] = df_final[col].astype(str).replace('None', '')

# --- 6. UPLOAD ---
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
records = df_final.to_dict(orient='records')

try:
    supabase.table("show_data_rough").upsert(records).execute()
    print(f"Jotform returned {len(submissions)} submissions.")
    print(f"Successfully synced {len(records)} rows to Supabase table: show_data_rough")
except Exception as e:
    print(f"Upload failed: {e}")