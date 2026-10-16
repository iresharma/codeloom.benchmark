import pytest
from playwright.sync_api import sync_playwright
def test_rendering():
  with sync_playwright() as p:
    browser = p.chromium.launch(args=['--no-sandbox','--disable-dev-shm-usage'])
    page = browser.new_page()
    errors=[]
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:3001/reader?memo=memos/alpha',wait_until='networkidle')
    assert page.locator('article').count()==1
    page.goto('http://127.0.0.1:3001/reader?memo=memos/absent',wait_until='networkidle')
    assert page.get_by_role('alert').inner_text()=='Memo not found'
    page.goto('http://127.0.0.1:3001/reader',wait_until='networkidle')
    assert page.locator('article').count()==2
    assert not errors, errors
    browser.close()
