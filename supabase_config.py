"""Public Supabase endpoint configuration shared by Jeffery clients.

The anon key is intentionally public and is restricted by the Edge Function's
authenticated account checks. Never put the service role key in a desktop app.
"""

SUPABASE_FUNCTION_URL = "https://tsidowtkxvozeczwhadz.supabase.co/functions/v1/jeffery-api"
SUPABASE_REGISTER_URL = "https://tsidowtkxvozeczwhadz.supabase.co/functions/v1/jeffery-register"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRzaWRvd3RreHZvemVjendoYWR6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTM4MTI1NTIsImV4cCI6MjEwOTM4ODU1Mn0.Ktg4tqJyg4LgnkEpjpZ7M3xJbxcVfnR1fPsuDfJDRhM"
