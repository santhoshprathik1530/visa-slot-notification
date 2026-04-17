# Atlys Italy Slot Notifier

This watcher polls Atlys' Schengen appointments API and is designed to run on a server.

It alerts when Italy appears for a new Indian city or when an existing city's earliest visible date moves earlier. It does not alert on later dates or disappearing slots.

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

Run the real watcher forever for Italy and alert on every poll if Bangalore has a slot:

```bash
set -a
source .env
set +a
python3 atlys_italy_notifier.py serve --watch-country italy --interval-seconds 300 --alert-mode always_when_present --required-city bangalore
```

Run a daily summary:

```bash
set -a
source .env
set +a
python3 atlys_italy_notifier.py daily-summary --watch-country italy
```

State is stored in:

- [.state/italy_slots_state.json](/Users/santhoshkasam/Desktop/Stuff/Others/visa slot notification/.state/italy_slots_state.json)
- [.state/austria_slots_state.json](/Users/santhoshkasam/Desktop/Stuff/Others/visa slot notification/.state/austria_slots_state.json)

## Deploy on a VPS with systemd or cron

Files included:

- [systemd/atlys-slot-watcher.service](/Users/santhoshkasam/Desktop/Stuff/Others/visa slot notification/systemd/atlys-slot-watcher.service)

Typical layout:

1. Copy the project to `/opt/atlys-slot-watcher`
2. Put your filled `.env` there
3. Copy the service file to `/etc/systemd/system/`
4. Run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now atlys-slot-watcher
sudo systemctl status atlys-slot-watcher
```

For your requested schedule, cron is simpler:

Every 5 minutes, alert whenever any slot exists:

```cron
2,7,12,17,22,27,32,37,42,47,52,57 * * * * cd /opt/atlys-slot-watcher && set -a && . ./.env && set +a && /usr/bin/python3 /opt/atlys-slot-watcher/atlys_italy_notifier.py check --watch-country italy --alert-mode always_when_present --required-city bangalore >> /opt/atlys-slot-watcher/logs/poll.log 2>&1
```

Daily at 9:00 AM IST summary:

```cron
13 * * * * cd /opt/atlys-slot-watcher && set -a && . ./.env && set +a && TZ=Asia/Kolkata /usr/bin/python3 /opt/atlys-slot-watcher/atlys_italy_notifier.py daily-summary --watch-country italy >> /opt/atlys-slot-watcher/logs/daily.log 2>&1
```

## Deploy with Docker

Build:

```bash
docker build -t atlys-slot-watcher .
```

Run:

```bash
docker run -d \
  --name atlys-slot-watcher \
  --restart unless-stopped \
  --env-file .env \
  -v "$(pwd)/.state:/app/.state" \
  atlys-slot-watcher
```

## Important setup notes

- Twilio WhatsApp requires WhatsApp sender setup and user opt-in. Outside an active conversation window, template rules apply.
- Twilio voice requires a voice-capable Twilio number.
- Telegram requires creating a bot and finding the target chat id.
- Pushover is a practical server-to-phone push option if you want push-style alerts without building your own mobile app.

## Source and API

The watcher uses the same Atlys API the site bundle calls:

`https://api.atlys.com/api/v2/application/slots/IT?...`
