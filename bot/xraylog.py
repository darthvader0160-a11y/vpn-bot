"""Меняет уровень журнала Xray в панели: python -m bot.xraylog debug|info|warning"""
import asyncio
import json
import sys

import aiohttp

from . import config


async def main(level: str):
    cfg = config.load()
    base = cfg.panel_url.rstrip("/") + "/panel/api/xray"
    headers = {"Authorization": f"Bearer {cfg.panel_token}", "Accept": "application/json"}
    async with aiohttp.ClientSession(headers=headers) as http:
        async with http.post(base + "/") as r:
            data = await r.json(content_type=None)
        obj = data["obj"]
        if isinstance(obj, str):
            obj = json.loads(obj)
        tpl = obj["xraySetting"]
        tpl.setdefault("log", {})["loglevel"] = level
        form = {"xraySetting": json.dumps(tpl), "outboundTestUrl": obj.get("outboundTestUrl", "")}
        async with http.post(base + "/update", data=form) as r:
            res = await r.json(content_type=None)
    print(f"Журнал Xray: {level}" if res.get("success") else f"Ошибка: {res.get('msg')}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "warning"))
