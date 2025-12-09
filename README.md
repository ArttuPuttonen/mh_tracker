# Matkahuolto Package Tracker

A Python-based automated monitoring system that tracks Matkahuolto shipments and sends intelligent alerts via Telegram when packages experience delays.

## Overview

This tool integrates with the Matkahuolto API to monitor package shipments in real-time. It automatically detects stale shipments based on Finnish business day calculations and sends consolidated Telegram notifications.

## Key Features

- **Flexible API Parsing**: Handles multiple Matkahuolto JSON response formats automatically
- **Smart Delay Detection**: Identifies packages stuck in transit using Finnish business day calculations
- **Consolidated Alerts**: Sends single, informative Telegram messages per run with complete shipment status
- **Configurable Monitoring**: Adjustable lookback periods and staleness thresholds
- **Retry Logic**: Built-in request retry mechanism for reliability

## Technical Highlights

- **API Integration**: REST API consumption with authentication and timeout handling
- **Data Normalization**: Robust parsing logic to handle 4+ different API response structures
- **Business Logic**: Custom Finnish business day calculations using `workalendar`
- **Error Handling**: Comprehensive timeout and retry mechanisms

## Configuration

The application uses environment variables for configuration:

```env
MH_USER=<matkahuolto_username>
MH_PASS=<matkahuolto_password>
TELEGRAM_TOKEN=<telegram_bot_token>
TELEGRAM_CHAT_ID=<telegram_chat_id>
LOOKBACK_DAYS=7
STALE_BUSINESS_DAYS=1
```

## Installation

```bash
# Clone the repository
git clone https://github.com/ArttuPuttonen/mh_tracker.git
cd mh_tracker

# Install dependencies
pip install requests python-telegram-bot workalendar python-dotenv

# Configure environment variables
cp env.txt .env
# Edit .env with your credentials
```

## Usage

```bash
python mh_tracker.py
```

The script can be scheduled with cron or similar tools for automated monitoring.

## Technologies Used

- **Python 3**: Core application language
- **Requests**: HTTP API client
- **python-telegram-bot**: Telegram notification integration
- **workalendar**: Finnish business day calculations

## How It Works

1. Fetches shipments from Matkahuolto API within the configured lookback period
2. Normalizes various API response formats into a consistent structure
3. Calculates business days since last shipment movement
4. Identifies stale shipments (excluding final delivery statuses)
5. Sends consolidated Telegram alert with actionable information

## Author

Arttu Puttonen

---

*This project demonstrates API integration, data processing, persistent storage, and automated alerting capabilities.*
