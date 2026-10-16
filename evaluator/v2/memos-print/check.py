import pytest
from playwright.sync_api import sync_playwright
def test_rendering():
  with sync_playwright() as p:
    browser = p.chromium.launch(args=['--no-sandbox','--disable-dev-shm-usage'])
    page = browser.new_page()
    errors=[]
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:3001/reader',wait_until='networkidle')
    page.locator('article').first.wait_for()
    assert page.locator('article table').count()>0
    assert page.locator('article pre').count()>0
    page.emulate_media(media='print')
    assert not page.locator('.reader-toolbar').is_visible()
    assert page.locator('.reader-meta').first.is_visible()
    assert page.locator('.reader-attachments').first.is_visible()
    assert page.locator('article').first.evaluate("e=>getComputedStyle(e).breakInside")=='avoid'
    assert not errors, errors
    browser.close()
