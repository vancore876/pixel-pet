# Jeffery AutoMate bridge

This directory contains the separate Supabase bridge and the LAN-side AutoMate connector. The bridge currently supports read-only part and order lookups against the restored staging database. Passwords, PINs, hashes, and credential fields are filtered and are never returned. Write operations are not enabled yet.

## Run the local connector

The current machine has a local ignored `.env` containing the bridge secret, public Supabase configuration, and a SQL login limited to five stored procedures on `FT_JefferyStaging_20261112`.

```powershell
python -m pip install -r automate_backend/requirements.txt
python automate_backend/bridge_agent.py
```

Keep this computer powered on and connected to the AutoMate SQL Server and the internet while the website needs AutoMate lookups. The bridge polls Supabase every few seconds; the website never connects directly to SQL Server.

## Supabase deployment

Project reference: `mholmzbdgteqewxsqszm` (`Jeffery AutoMate Bridge`, `us-east-1`). The migration and `automate-bridge` function are deployed separately from Jeffery's existing accounts, notes, and orders backend.

Apply later migrations and deploy function changes from the repository root:

```powershell
npx supabase db push --workdir automate_backend --remote=mholmzbdgteqewxsqszm
npx supabase functions deploy automate-bridge --workdir automate_backend --remote=mholmzbdgteqewxsqszm --use-api --no-verify-jwt
```
