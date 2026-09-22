import asyncio, os
from dotenv import load_dotenv
load_dotenv()

async def test():
    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        api_key=os.getenv('OPENROUTER_API_KEY'),
        base_url=os.getenv('OPENROUTER_BASE_URL'),
        default_headers={'X-OpenRouter-Title': 'AI Service'},
        timeout=10.0,
    )
    try:
        completion = await client.chat.completions.create(
            model=os.getenv('OPENROUTER_MODEL'),
            messages=[
                {'role': 'system', 'content': 'You are a logistics exception analyst. Be concise and operational.'},
                {'role': 'user', 'content': (
                    'Classify this shipment exception. Return one valid JSON object, with no markdown. '
                    'It must exactly follow this shape: '
                    '{"structuredRecord":{"severity":"LOW|HIGH|CRITICAL",'
                    '"category":"VEHICLE_ISSUE|CUSTOMER_ABSENT|WEATHER","etaImpact":"string"},'
                    '"actionPlan":"string","customerNotification":"string"}. '
                    'Shipment ID: TEST-002. Exception: ta9s khayeb barsha w7elt fil kayes denya ghar9a'
                )},
            ],
            response_format={'type': 'json_object'},
        )
        print('SUCCESS:', completion.choices[0].message.content)
    except Exception as e:
        print('ERROR type:', type(e).__name__)
        print('ERROR:', e)

asyncio.run(test())
