from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.environ.get("XIAOMI_API_KEY"),
    base_url=os.environ.get("XIAOMI_BASE_URL", "https://platform.beeknoee.com/api/v1")
)

response = client.chat.completions.create(
    model=os.environ.get("XIAOMI_MODEL", "MiMo-V2.5-Pro"),
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Code bloom sort "}
    ],
    max_tokens=1000,
    temperature=0.7
)

print(response.choices[0].message.content)
