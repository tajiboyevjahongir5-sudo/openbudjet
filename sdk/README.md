# Open Budget Uzbekistan API SDK

Official Python client for the Open Budget Uzbekistan API Gateway.

## Installation

```bash
pip install openbudget-api
```

## Quickstart

```python
import asyncio
from openbudget_api import OpenBudgetClient

async def main():
    # Obtain your API key from Telegram Bot: @Budjetuz2026_Bot
    client = OpenBudgetClient(api_key="ob_api_your_key_here")
    
    # Get initiative details
    initiative = await client.get_initiative(project_id="32541")
    print("Project info:", initiative)

if __name__ == "__main__":
    asyncio.run(main())
```

## Get an API Key

Start the official Telegram Bot: [@Budjetuz2026_Bot](https://t.me/Budjetuz2026_Bot) and choose **🤝 Hamkorlik & API** to purchase voting balance.
