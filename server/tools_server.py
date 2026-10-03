import os
import socket
import ipaddress
from urllib.parse import urlparse, urljoin

from dotenv import load_dotenv
load_dotenv()
import httpx
from bs4 import BeautifulSoup
from tavily import TavilyClient
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("research-tools")
tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])

MAX_CHARS = 20_000          # cap on text returned to the agent
MAX_BYTES = 2_000_000       # cap on downloaded page size
USER_AGENT = os.getenv("FETCH_USER_AGENT", "research-agent/0.1")


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
    try:
        results = tavily.search(query=query, max_results=5, search_depth="basic")["results"]
    except Exception as e:
        raise ToolError(f"search failed: {type(e).__name__}")
    return "\n".join(f"- {r['title']} | {r['url']} | {(r.get('content') or '')[:300]}" for r in results)


@mcp.tool()
def fetch_url(url: str) -> str:
    """Fetch a public web page and return its cleaned text (truncated)."""
    current = url
    for _ in range(4):
        if not is_public_url(current):
            raise ToolError("blocked: URL is not a public http(s) address")
        try:
            response = httpx.get(current, timeout=15, follow_redirects=False,
                                 headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError as e:
            raise ToolError(f"could not fetch: {type(e).__name__}")
        if not response.is_redirect:
            break
        current = urljoin(current, response.headers.get("location", ""))
    else:
        raise ToolError("too many redirects")

    if response.status_code >= 400:
        raise ToolError(f"HTTP {response.status_code} from that site")

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        raise ToolError(f"unsupported content type: {content_type or 'unknown'} (PDFs are not supported)")
    if len(response.content) > MAX_BYTES:
        raise ToolError("page too large")

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())[:MAX_CHARS]


if __name__ == "__main__":
    mcp.run(transport="stdio")   # never use print() here: it corrupts the protocol