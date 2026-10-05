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
from io import BytesIO
from pypdf import PdfReader

mcp = MCPServer("research-tools")
tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])

MAX_CHARS = 50_000          # cap on text returned to the agent
MAX_BYTES = 10_000_000       # cap on downloaded page size
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


def get_page(url):
    """One request, with a single retry on timeout."""
    for attempt in range(2):
        try:
            return httpx.get(url, timeout=20, follow_redirects=False,
                             headers={"User-Agent": USER_AGENT})
        except httpx.TimeoutException:
            if attempt == 1:
                raise ToolError("timed out twice")
        except httpx.HTTPError as e:
            raise ToolError(f"Could not fetch: {type(e).__name__}")


@mcp.tool()
def fetch_url(url: str) -> str:
    """Fetch a public web page or PDF and return its text (truncated)."""
    current = url
    for _ in range(4):
        if not is_public_url(current):
            raise ToolError("blocked: URL is not a public http(s) address")
        response = get_page(current)
        if not response.is_redirect:
            break
        current = urljoin(current, response.headers.get("location", ""))
    else:
        raise ToolError("too many redirects")

    if response.status_code >= 400:
        raise ToolError(f"HTTP {response.status_code} from that site")
    if len(response.content) > MAX_BYTES:
        raise ToolError("page too large")

    content_type = response.headers.get("content-type", "")
    if "pdf" in content_type:
        try:
            reader = PdfReader(BytesIO(response.content))
            text = " ".join((page.extract_text() or "") for page in reader.pages[:15])
        except Exception:
            raise ToolError("could not read this PDF")
        return " ".join(text.split())[:MAX_CHARS]

    if "html" not in content_type and "text" not in content_type:
        raise ToolError(f"unsupported content type: {content_type or 'unknown'}")

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())[:MAX_CHARS]


if __name__ == "__main__":
    mcp.run(transport="stdio")