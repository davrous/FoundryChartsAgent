"""Refresh the pinned Copilot MCP schema from the actual FastMCP registration."""

import asyncio
import json
from pathlib import Path

from gateway.config import Settings
from gateway.main import create_app


async def main() -> None:
    app = create_app(Settings(dev_mode=True))
    tools = await app.state.mcp.list_tools()
    path = Path(__file__).resolve().parents[1] / "appPackage" / "mcp-tools.json"
    path.write_text(
        json.dumps({"tools": [tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in tools]}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Exported {len(tools)} tool definitions to {path}")


if __name__ == "__main__":
    asyncio.run(main())
