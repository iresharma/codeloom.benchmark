import pytest
from playwright.sync_api import sync_playwright
def test_rendering():
  with sync_playwright() as p:
    browser = p.chromium.launch(args=['--no-sandbox','--disable-dev-shm-usage'])
    page = browser.new_page()
    errors=[]
    page.on('pageerror', lambda error: errors.append(str(error)))
    for width in (375,1280):
      page.set_viewport_size({'width':width,'height':800})
      page.goto('http://127.0.0.1:3001/reader',wait_until='networkidle')
      page.locator('article').first.wait_for()
      assert page.locator('article').count()==2
      assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
      assert page.locator('article a').first.is_visible()
    assert not errors, errors
    browser.close()
