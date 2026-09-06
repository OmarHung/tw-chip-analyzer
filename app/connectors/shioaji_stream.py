"""
Minimal Shioaji adapter.
Keep callbacks lightweight: enqueue events and process them in another worker.
"""
import os
from queue import Queue
from dotenv import load_dotenv

load_dotenv()
event_queue = Queue(maxsize=200000)

def build_api():
    import shioaji as sj
    api = sj.Shioaji()
    api.login(
        api_key=os.environ["SJ_API_KEY"],
        secret_key=os.environ["SJ_SEC_KEY"],
    )
    return api

def subscribe_stock(api, code: str):
    import shioaji as sj
    contract = api.contracts.get(code)

    @api.on_tick_stk_v1()
    def on_tick(exchange, tick):
        event_queue.put_nowait(("tick", tick))

    @api.on_bidask_stk_v1()
    def on_bidask(exchange, quote):
        event_queue.put_nowait(("bidask", quote))

    api.quote.subscribe(contract, quote_type=sj.constant.QuoteType.Tick, version=sj.constant.QuoteVersion.v1)
    api.quote.subscribe(contract, quote_type=sj.constant.QuoteType.BidAsk, version=sj.constant.QuoteVersion.v1)
    return contract
