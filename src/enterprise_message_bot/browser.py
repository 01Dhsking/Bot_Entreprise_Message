import asyncio
import logging
import os
import shutil
import unicodedata
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .config import get_settings
from .schemas import RegistryCompany, RegistryPage

log = logging.getLogger(__name__)
settings = get_settings()

SOURCE_TYPES = ("companies", "establishments")

_playwright: Playwright | None = None
_context: BrowserContext | None = None
_pages: dict[str, Page] = {}
_browser_lock = asyncio.Lock()
_action_lock = asyncio.Lock()

_DOCKER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-setuid-sandbox",
    "--window-size=1440,1000",
]

_READ_TABLE_SCRIPT = r"""
(() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const rows = Array.from(document.querySelectorAll('[role="row"]'));
  const headers = rows.length
    ? Array.from(rows[0].querySelectorAll('[role="columnheader"]'))
        .map((cell) => clean(cell.innerText))
    : [];
  const values = rows.slice(1).map((row) =>
    Array.from(row.querySelectorAll('[role="cell"]')).map((cell) => clean(cell.innerText))
  ).filter((cells) => cells.length && cells[0] && !/^\d+(\s+\d+)+$/.test(cells.join(' ')));

  const bodyText = clean(document.body ? document.body.innerText : '');
  const pageInput = document.querySelector('[role="spinbutton"], input[type="number"]');
  const pageSizeSelect = Array.from(document.querySelectorAll('select')).pop();
  const totalPagesMatch = bodyText.match(/Page(?:\s+\d+)?\s+(?:of|de)\s+([\d,\s]+)/i);
  const totalRecordsMatch = bodyText.match(
    /(?:Showing|pagination\.showing)\s+[\d,\s]+\s+(?:of|pagination\.of-records)\s+([\d,\s]+)\s+(?:records|pagination\.records)/i
  );

  return {
    url: window.location.href,
    title: document.title,
    page_number: pageInput && pageInput.value ? Number(pageInput.value) : null,
    total_pages: totalPagesMatch ? Number(totalPagesMatch[1].replace(/\D/g, '')) : null,
    total_records: totalRecordsMatch
      ? Number(totalRecordsMatch[1].replace(/\D/g, ''))
      : null,
    headers,
    rows: values,
    raw_metadata: {
      row_count: values.length,
      page_size: pageSizeSelect && pageSizeSelect.value
        ? Number(pageSizeSelect.value)
        : values.length,
      has_search: !!document.querySelector(
        'input[placeholder="Search..."], input[placeholder="Chercher"]'
      ),
      loaded_at: new Date().toISOString()
    }
  };
})()
"""


def validate_source_type(source_type: str) -> str:
    normalized = source_type.strip().lower()
    if normalized not in SOURCE_TYPES:
        raise ValueError("source_type must be 'companies' or 'establishments'")
    return normalized


def _normalize_header(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_value.casefold().split())


def _record_from_cells(
    source_type: str, headers: list[str], cells: list[str]
) -> RegistryCompany | None:
    normalized_headers = [_normalize_header(header) for header in headers]
    values = {
        header: cells[index] if index < len(cells) else ""
        for index, header in enumerate(normalized_headers)
    }

    def cell(*names: str) -> str:
        for name in names:
            value = values.get(name, "")
            if value:
                return value
        return ""

    if source_type == "companies":
        legal_name = cell("denomination")
        trade_name = cell("nom commercial")
        owner_first_name = ""
        owner_last_name = ""
    else:
        trade_name = cell("nom commercial")
        owner_first_name = cell("prenom")
        owner_last_name = cell("nom")
        legal_name = trade_name or " ".join(
            part for part in (owner_first_name, owner_last_name) if part
        )

    if not legal_name:
        return None

    return RegistryCompany(
        source_type=source_type,
        legal_name=legal_name,
        trade_name=trade_name,
        creation_date=cell("date creation", "date de creation"),
        registration_number=cell("numero rc"),
        activity=cell("activite principale", "activite"),
        owner_first_name=owner_first_name,
        owner_last_name=owner_last_name,
        city=cell("commune"),
        district=cell("quartier"),
        phone=cell("telephone"),
        email=cell("email"),
    )


def parse_rendered_table(source_type: str, data: dict[str, Any]) -> RegistryPage:
    headers = [str(value) for value in data.get("headers", [])]
    companies = [
        record
        for cells in data.get("rows", [])
        if (record := _record_from_cells(source_type, headers, [str(value) for value in cells]))
    ]
    return RegistryPage(
        source_type=source_type,
        url=data.get("url", settings.registry_url_for(source_type)),
        title=data.get("title", ""),
        page_number=data.get("page_number"),
        total_pages=data.get("total_pages"),
        total_records=data.get("total_records"),
        companies=companies,
        raw_metadata={**data.get("raw_metadata", {}), "headers": headers},
    )


def _resolve_chrome_path() -> str | None:
    if settings.chrome_path:
        return settings.chrome_path
    candidates = [
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        shutil.which("chrome"),
        shutil.which("msedge"),
        str(Path(os.getenv("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.getenv("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe"),
        str(Path(os.getenv("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
    ]
    return next(
        (candidate for candidate in candidates if candidate and Path(candidate).is_file()), None
    )


async def _ensure_context() -> BrowserContext:
    global _playwright, _context
    if _context is None:
        Path(settings.browser_data_dir).mkdir(parents=True, exist_ok=True)
        browser_args = _DOCKER_ARGS if settings.chrome_path else ["--window-size=1440,1000"]
        _playwright = await async_playwright().start()
        _context = await _playwright.chromium.launch_persistent_context(
            user_data_dir=str(settings.browser_data_dir),
            headless=settings.headless,
            executable_path=_resolve_chrome_path(),
            args=browser_args,
            locale="fr-FR",
            viewport={"width": 1440, "height": 1000},
        )
    return _context


async def _navigate(page: Page, url: str) -> None:
    try:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=settings.navigation_timeout_seconds * 1000,
        )
    except PlaywrightTimeoutError:
        log.warning("Navigation timeout for %s; checking the rendered DOM", url)
    await page.wait_for_selector('[role="row"]', timeout=settings.navigation_timeout_seconds * 1000)
    await asyncio.sleep(settings.page_settle_seconds)


async def get_registry_page(source_type: str, *, refresh: bool = False) -> Page:
    source_type = validate_source_type(source_type)
    async with _browser_lock:
        context = await _ensure_context()
        page = _pages.get(source_type)
        if page is None or page.is_closed():
            page = await context.new_page()
            _pages[source_type] = page
            await _navigate(page, settings.registry_url_for(source_type))
        elif refresh:
            await _navigate(page, settings.registry_url_for(source_type))
        return page


async def _set_page_size(page: Page, source_type: str, page_size: int) -> None:
    if page_size not in {10, 15, 20, 30, 50, 100, 200, 1000}:
        raise ValueError("page_size must be one of 10, 15, 20, 30, 50, 100, 200 or 1000")
    selector = page.get_by_role("combobox").last
    label = f"{page_size} rows" if source_type == "companies" else f"{page_size} lignes"
    try:
        await selector.select_option(label=label)
    except Exception:
        await selector.select_option(value=str(page_size))
    await asyncio.sleep(max(settings.action_delay_seconds, settings.page_settle_seconds))


async def _set_page_number(page: Page, page_number: int) -> None:
    if page_number < 1:
        raise ValueError("page_number must be greater than zero")
    page_input = page.get_by_role("spinbutton")
    await page_input.fill(str(page_number))
    await page_input.press("Enter")
    await asyncio.sleep(max(settings.action_delay_seconds, settings.page_settle_seconds))


async def _read_page(page: Page, source_type: str) -> RegistryPage:
    rendered = parse_rendered_table(source_type, await page.evaluate(_READ_TABLE_SCRIPT))
    if not rendered.companies:
        raise RuntimeError(
            f"The {source_type} directory loaded but no record row was found. "
            "The portal layout may have changed."
        )
    return rendered


async def read_registry_page(
    source_type: str,
    *,
    refresh: bool = False,
    page_number: int | None = None,
    page_size: int | None = None,
) -> RegistryPage:
    source_type = validate_source_type(source_type)
    async with _action_lock:
        page = await get_registry_page(source_type, refresh=refresh)
        if page_size is not None:
            await _set_page_size(page, source_type, page_size)
        if page_number is not None:
            await _set_page_number(page, page_number)
        return await _read_page(page, source_type)


async def search_registry(source_type: str, query: str) -> RegistryPage:
    source_type = validate_source_type(source_type)
    normalized = query.strip()
    if not normalized:
        raise ValueError("query cannot be empty")
    async with _action_lock:
        page = await get_registry_page(source_type)
        search_input = page.locator('input[placeholder="Search..."], input[placeholder="Chercher"]')
        if await search_input.count() == 0:
            raise RuntimeError(f"The {source_type} search input was not found")
        await search_input.fill(normalized)
        await asyncio.sleep(max(settings.action_delay_seconds, settings.page_settle_seconds))
        result = await _read_page(page, source_type)
        result.raw_metadata["query"] = normalized
        return result


def browser_status() -> dict[str, Any]:
    return {
        "started": _context is not None,
        "open_sources": sorted(_pages),
        "headless": settings.headless,
        "registry_urls": {
            source_type: settings.registry_url_for(source_type) for source_type in SOURCE_TYPES
        },
    }


async def stop_browser() -> None:
    global _playwright, _context, _pages
    if _context is not None:
        try:
            await _context.close()
        except Exception as exc:
            log.warning("Unable to stop Chromium cleanly: %s", exc)
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception as exc:
            log.warning("Unable to stop Playwright cleanly: %s", exc)
    _playwright = None
    _context = None
    _pages = {}
