# Atlys Italy Slot Notifier

This watcher polls Atlys' Schengen appointments API and is currently intended to run on a GCP VM.

Current production behavior:

- every 5 minutes: check only `Italy -> Bangalore`
- send alert only when a Bangalore slot is visible
- twice daily at `9:00 AM IST` and `9:00 PM IST`: send a Bangalore summary
- email is the reliable channel
- WhatsApp works as a normal Twilio sandbox message while the 24-hour window is active

## Channels supported from a server

- Email via SMTP
- Telegram bot messages
- SMS via Twilio
- WhatsApp via Twilio
- Voice phone calls via Twilio
- Pushover push notifications
- Generic webhooks
- Slack incoming webhooks
- Discord webhooks

## Channels not directly possible from a generic server

- iMessage: not available as a normal server-side API
- Native iPhone/Android push without an app: you need your own app plus APNs or FCM device tokens

If you want true native mobile push later, the clean route is a tiny app that registers with APNs or FCM and gives this server a device token.

## Quick start

For a minimal first deployment, use only:

- `email`
- `whatsapp`

Copy the sample config and fill only the channels you actually want:

```bash
cp .env.example .env
```

Run one test check without sending alerts:

```bash
set -a
source .env
set +a
python3 atlys_italy_notifier.py check --watch-country austria --no-notify
```

Run a live test alert with Austria:

```bash
set -a
source .env
set +a
python3 atlys_italy_notifier.py check --watch-country austria
```

Run a one-time Bangalore poll:

```bash
set -a
source .env
set +a
python3 atlys_italy_notifier.py check --watch-country italy --alert-mode always_when_present --required-city bangalore
```

Run a one-time Bangalore summary:

```bash
set -a
source .env
set +a
python3 atlys_italy_notifier.py daily-summary --watch-country italy --required-city bangalore
```

State is stored in:

- [.state/italy_slots_state.json](/Users/santhoshkasam/Desktop/Stuff/Others/visa slot notification/.state/italy_slots_state.json)
- [.state/austria_slots_state.json](/Users/santhoshkasam/Desktop/Stuff/Others/visa slot notification/.state/austria_slots_state.json)

## Current VM setup

Files included:

- [systemd/visa-slot-dashboard.service](/Users/santhoshkasam/Desktop/Stuff/Others/visa slot notification/systemd/visa-slot-dashboard.service)

Project path on the VM:

```bash
/home/kasamsanthoshprathik/visa-slot-notification
```

### Cron

The intended final crontab is:

```cron
CRON_TZ=Asia/Kolkata
*/5 * * * * cd /home/kasamsanthoshprathik/visa-slot-notification && set -a && . ./.env && set +a && /usr/bin/python3 /home/kasamsanthoshprathik/visa-slot-notification/atlys_italy_notifier.py check --watch-country italy --alert-mode always_when_present --required-city bangalore --run-source cron >> /home/kasamsanthoshprathik/visa-slot-notification/logs/poll.log 2>&1
0 9,21 * * * cd /home/kasamsanthoshprathik/visa-slot-notification && set -a && . ./.env && set +a && /usr/bin/python3 /home/kasamsanthoshprathik/visa-slot-notification/atlys_italy_notifier.py daily-summary --watch-country italy --required-city bangalore --run-source cron >> /home/kasamsanthoshprathik/visa-slot-notification/logs/daily.log 2>&1
30 23 * * * cp /home/kasamsanthoshprathik/visa-slot-notification/data/runs.db /home/kasamsanthoshprathik/visa-slot-notification/backups/runs-$(date +\%F).db
```

### Dashboard service

To run the dashboard permanently on the VM:

```bash
cd ~/visa-slot-notification
sudo cp systemd/visa-slot-dashboard.service /etc/systemd/system/
sudo sed -i 's|/opt/atlys-slot-watcher|/home/kasamsanthoshprathik/visa-slot-notification|g' /etc/systemd/system/visa-slot-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now visa-slot-dashboard
sudo systemctl status visa-slot-dashboard
```

## Important setup notes

- Twilio WhatsApp in the current setup uses normal sandbox messages, not production templates.
- To keep WhatsApp working, each recipient must message the Twilio sandbox number within the 24-hour window.
- The sandbox inbound auto-reply can be silenced by pointing the Twilio sandbox inbound webhook to `/twilio/inbound` on the dashboard server.
- Twilio voice requires a voice-capable Twilio number.
- Telegram requires creating a bot and finding the target chat id.
- Pushover is a practical server-to-phone push option if you want push-style alerts without building your own mobile app.

## Run logging and dashboard

Every notifier run is now stored in SQLite at:

- `data/runs.db`

The DB stores:

- created time
- command type
- source (`manual` or `cron`)
- city filter
- alert mode
- current slots
- changes
- delivery results
- errors

A lightweight dashboard server is included:

```bash
python3 dashboard.py
```

It serves:

- `/` HTML dashboard
- `/api/runs` JSON API
- `/twilio/inbound` silent inbound webhook for Twilio sandbox replies

Example on a VM:

```bash
cd ~/visa-slot-notification
python3 dashboard.py
```

Then open:

- `http://YOUR_VM_IP:8080`

If using GCP, create a firewall rule or expose port `8080` on the VM.

## Source and API

The watcher uses the same Atlys API the site bundle calls:

`https://api.atlys.com/api/v2/application/slots/IT?...`
