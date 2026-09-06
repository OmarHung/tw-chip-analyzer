import httpx

URL = "https://openapi.tdcc.com.tw/v1/opendata/1-5"

async def fetch_shareholder_distribution():
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(URL)
        r.raise_for_status()
        return r.json()
