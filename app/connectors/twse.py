import httpx

BASE = "https://www.twse.com.tw"

async def fetch_institutional(date_yyyymmdd: str):
    url = f"{BASE}/rwd/zh/fund/T86"
    params = {
        "date": date_yyyymmdd,
        "selectType": "ALLBUT0999",
        "response": "json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()

async def fetch_margin(date_yyyymmdd: str):
    url = f"{BASE}/rwd/zh/marginTrading/MI_MARGN"
    params = {
        "date": date_yyyymmdd,
        "selectType": "STOCK",
        "response": "json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()
