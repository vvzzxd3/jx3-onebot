"""JX3BOX 开服状态查询，不持久化数据或参与天气请求计数。"""
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

SERVER_STATE_URL = "https://spider2.jx3box.com/api/spider/server/server_state"
_UNAVAILABLE = "暂时无法查询开服状态，请稍后再试"


class ServerStateService:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def query(self, args: str) -> str:
        name = args.strip()
        if not name:
            name = "绝代天骄"
            # return "请输入服务器名称，例如：开服 青梅煮酒"
        if (len(name) > 80 or any(
                char.isspace() or ord(char) < 32 or ord(char) == 127
                or char in "<>@:/\\" for char in name)):
            return "服务器名称无效，请输入完整服务器名称，例如：开服 青梅煮酒"
        try:
            response = await self.client.get(SERVER_STATE_URL)
            response.raise_for_status()
            servers = response.json()
        except (httpx.HTTPError, ValueError):
            return _UNAVAILABLE
        if not isinstance(servers, list) or any(not isinstance(row, dict) for row in servers):
            return _UNAVAILABLE
        for server in servers:
            if server.get("server_name") != name:
                continue
            connected = server.get("connect_state")
            if not isinstance(connected, bool):
                return _UNAVAILABLE
            timestamp = server.get("maintain_time")
            # bool is an int subclass, but is not a valid Unix timestamp here.
            formatted = "不可用"
            if type(timestamp) in (int, float):
                try:
                    maintained = datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Shanghai"))
                    formatted = maintained.strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, OverflowError, OSError):
                    pass
            return (f"{name}已开服\n开服时间：{formatted}" if connected
                    else f"{name}维护中\n维护时间：{formatted}")
        return f"未找到服务器“{name}”，请检查服务器名称"
