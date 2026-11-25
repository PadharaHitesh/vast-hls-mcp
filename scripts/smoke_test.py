"""End-to-end check: launch the server over stdio and call tools like an AI client would."""

import asyncio
import sys
from pathlib import Path

from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent
CUE = "/DAvAAAAAAAA///wFAVIAACPf+/+c2nALv4AUsz1AAAAAAAKAAhDVUVJAAABNWLbowo="


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "vast_hls_mcp.server"])
    async with Client(params) as client:
        tools = await client.list_tools()
        print("tools:", ", ".join(sorted(t.name for t in tools.tools)))

        res = await client.call_tool("decode_scte35", {"payload": CUE})
        print("decode_scte35:", res.structured_content["summary"])

        playlist = (ROOT / "examples" / "cue_out_vod.m3u8").read_text()
        res = await client.call_tool("find_ad_breaks", {"source": playlist})
        out = res.structured_content
        print(f"find_ad_breaks: {out['break_count']} breaks, healthy={out['healthy']}")
        for issue in out["issues"]:
            print("  -", issue)


if __name__ == "__main__":
    asyncio.run(main())
