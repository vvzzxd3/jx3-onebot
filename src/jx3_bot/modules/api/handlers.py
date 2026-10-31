"""开服命令注册；回复由应用统一经过 outbox 投递。"""
from ...routing import Inject, on_group, on_private
from .service import ServerStateService


@on_group(match="开服")
@on_group(match="开服", at_me=True)
@on_private(match="开服")
async def query(args: str, server_state: ServerStateService = Inject("server_state")):
    return await server_state.query(args)
