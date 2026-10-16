import pytest
from playwright.sync_api import sync_playwright
def test_rendering():
  with sync_playwright() as p:
    browser = p.chromium.launch(args=['--no-sandbox','--disable-dev-shm-usage'])
    page = browser.new_page()
    errors=[]
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:3001/reader?q=ALPHA&tag=release',wait_until='networkidle')
    assert page.locator('article').count()==1
    assert page.get_by_role('status').inner_text()=='1 memo'
    assert page.locator('mark').count()>0
    page.goto('http://127.0.0.1:3001/reader?q=beta&tag=release',wait_until='networkidle')
    assert page.locator('article').count()==0
    assert page.get_by_text('No memos found',exact=True).is_visible()
    page.goto('http://127.0.0.1:3001/reader?q=%20beta%20',wait_until='networkidle')
    assert page.locator('article').count()==1
    assert not errors, errors
    browser.close()
