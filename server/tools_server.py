import os
import socket
import ipaddress
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv()
import httpx
from bs4 import BeautifulSoup
from tavily import TavilyClient
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("research-tools")
tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])

MAX_CHARS = 20_000          # cap on text returned to the agent
MAX_BYTES = 2_000_000       # cap on downloaded page size


def is_public_url(url: str) -> bool:
    """Block non-http(s) URLs and anything pointing at private/internal addresses."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        addresses = socket.getaddrinfo(parts.hostname, None)
    except socket.gaierror:
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


@mcp.tool()
def web_search(query: str) -> str:
    """Search the web. Returns up to 5 results as: title | url | snippet."""
    results = tavily.search(query=query, max_results=5, search_depth="basic")["results"]
    return "\n".join(f"- {r['title']} | {r['url']} | {r['content'][:300]}" for r in results)


@mcp.tool()
def fetch_url(url: str) -> str:
    """Fetch a public web page and return its cleaned text (truncated)."""
    if not is_public_url(url):
        raise ValueError("blocked: URL is not a public http(s) address")

    response = httpx.get(url, timeout=15, follow_redirects=False,
                         headers={"User-Agent": "research-agent/0.1"})

    if response.is_redirect:
        # not followed automatically so the new URL gets checked too
        raise ValueError(f"redirected to {response.headers.get('location')}; call fetch_url on that URL")
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        raise ValueError("unsupported content type (only HTML or text pages)")
    if len(response.content) > MAX_BYTES:
        raise ValueError("page too large")

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return text[:MAX_CHARS]


if __name__ == "__main__":
    mcp.run(transport="stdio")   # never use print() here: it corrupts the protocol