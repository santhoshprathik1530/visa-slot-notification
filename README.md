# Alerting and Monitoring System

A small Python-based alerting system that polls an external data source, evaluates custom alert conditions, sends notifications across multiple channels, stores run history in SQLite, and exposes a lightweight dashboard for operations visibility.

This repository currently includes an Atlys-based visa-slot watcher as the concrete implementation, but the structure is generic enough to present as a portfolio project for:

- polling and monitoring jobs
- multi-channel notifications
- scheduled automation
- SQLite-backed operational logging
- simple internal dashboards

## What It Does

At a high level, the project:

1. polls an upstream API
2. extracts and normalizes the relevant availability data
3. applies alert rules
4. sends notifications through configured channels
5. logs every run into SQLite
6. serves an HTML dashboard and JSON API for inspection

## Current Capabilities

- Polling against the Atlys appointments API
- Country filtering
- Optional city-specific filtering
- Two alert modes:
  - `always_when_present`
  - `daily_summary`
- Multi-channel delivery:
  - Email via SMTP
  - WhatsApp via Twilio
  - SMS via Twilio
  - Voice via Twilio
  - Telegram
  - Slack webhook
  - Discord webhook
  - Generic webhook
  - Pushover
- Run history stored in SQLite
- Dashboard with:
  - latest summary run
  - latest poll run
  - last seen slot timestamp
  - run filtering
  - timezone toggle
  - delivery/error inspection

## Project Structure

- [atlys_italy_notifier.py](/Users/santhoshkasam/Desktop/Stuff/Others/visa slot notification/atlys_italy_notifier.py)
  Core polling and notification engine
- [dashboard.py](/Users/santhoshkasam/Desktop/Stuff/Others/visa slot notification/dashboard.py)
  Lightweight HTML and JSON dashboard
- [.env.example](/Users/santhoshkasam/Desktop/Stuff/Others/visa slot notification/.env.example)
  Example environment configuration
- [systemd/visa-slot-dashboard.service](/Users/santhoshkasam/Desktop/Stuff/Others/visa slot notification/systemd/visa-slot-dashboard.service)
  Example systemd unit for the dashboard

## Example Use Case

One production-style configuration for this project is:

- poll every 5 minutes
- monitor only `Italy -> Bangalore`
- alert only when a slot is visible
- send summary updates twice daily
- deliver via email and WhatsApp
- log every run to SQLite
- expose a dashboard on a VM

That is just one configuration. The engine itself is more general.

## Quick Start

Copy the sample environment:

```bash
cp .env.example .env
```

Load the environment and run a one-time check:

```bash
set -a
source .env
set +a
python3 atlys_italy_notifier.py check --watch-country italy
```

Run a city-specific poll:

```bash
set -a
source .env
set +a
python3 atlys_italy_notifier.py check --watch-country italy --alert-mode always_when_present --required-city bangalore
```

Run a summary:

```bash
set -a
source .env
set +a
python3 atlys_italy_notifier.py daily-summary --watch-country italy --required-city bangalore
```

## Configuration Model

The notifier is driven by environment variables.

Common examples:

- `ALERT_CHANNELS=email,whatsapp`
- `SMTP_*` for email
- `TWILIO_*` for Twilio delivery
- `EMAIL_TO` for email recipients
- `TWILIO_WHATSAPP_TO` for WhatsApp recipients

The project supports multiple recipients via comma-separated values.

## Scheduling

The project does not require an internal scheduler. It is intended to be triggered by:

- cron
- systemd timers
- GitHub Actions
- other schedulers

Example cron:

```cron
CRON_TZ=Asia/Kolkata
*/5 * * * * cd /path/to/project && set -a && . ./.env && set +a && /usr/bin/python3 /path/to/project/atlys_italy_notifier.py check --watch-country italy --alert-mode always_when_present --required-city bangalore --run-source cron >> /path/to/project/logs/poll.log 2>&1
0 9,21 * * * cd /path/to/project && set -a && . ./.env && set +a && /usr/bin/python3 /path/to/project/atlys_italy_notifier.py daily-summary --watch-country italy --required-city bangalore --run-source cron >> /path/to/project/logs/daily.log 2>&1
```

## Logging and Persistence

Every run is written into SQLite:

- `data/runs.db`

Logged fields include:

- run time
- command type
- source (`manual` or `cron`)
- alert mode
- city filter
- current slot snapshot
- detected changes
- delivery results
- errors
- exit code

This makes the project suitable for basic auditability and debugging without external infrastructure.

## Dashboard

Start the dashboard:

```bash
python3 dashboard.py
```

It exposes:

- `/` HTML dashboard
- `/api/runs` JSON API
- `/twilio/inbound` silent webhook for Twilio sandbox inbound replies

The UI includes:

- latest summary run
- latest poll run
- run history table
- source labels
- timezone toggle
- filtering between summary and poll runs

## Deployment Patterns

This project works well on:

- a small VM
- a home server
- a low-cost cloud instance

Typical setup:

1. clone the repo onto a VM
2. configure `.env`
3. add cron jobs
4. run the dashboard with systemd
5. optionally back up `data/runs.db`

## Notes on WhatsApp

For Twilio WhatsApp sandbox usage:

- free-form outbound messages only work while the 24-hour customer window is active
- users may need to message the sandbox periodically to keep the window alive
- inbound auto-replies can be silenced by pointing Twilio inbound handling to `/twilio/inbound`

For production-grade WhatsApp outside the 24-hour window, proper template messaging is required.

## Why This Makes a Good Portfolio Project

This repository demonstrates:

- practical polling and monitoring logic
- external API integration
- alert fanout across multiple delivery channels
- scheduling and automation patterns
- persistent operational logging
- lightweight observability tooling
- deployability on a simple VM

## Source Data

The current concrete implementation uses the Atlys appointments API:

`https://api.atlys.com/api/v2/application/slots/IT?...`

The overall structure can be adapted to other alerting or availability-monitoring use cases.
