"""T12.4: the lifespan loads MCP tools and checks schema pins before serving."""

from __future__ import annotations


async def test_connect_tools_verifies_pins_at_startup(app) -> None:
    from mcp.client.client import Client

    from app.bootstrap.container import connect_tools
    from mcp_servers import network_gateway, payments

    c = app.state.container
    saved = dict(c.mcp)
    try:
        gw = await connect_tools(
            c,
            factories={
                "payments": lambda: Client(payments.create_server(payments.PaymentsLedger.from_seeds())),
                "network-gateway": lambda: Client(network_gateway.create_server(network_gateway.NetworkGatewayStore())),
            },
        )
        assert gw.disabled == {} and all(conn.started for conn in c.mcp.values())
    finally:
        for conn in c.mcp.values():
            await conn.close()
        c.mcp.clear()
        c.mcp.update(saved)
