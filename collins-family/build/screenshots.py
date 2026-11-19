#!/usr/bin/env python3
"""Capture full-tree verification screenshots into /workspace/gramps-web/screens/full_*.png."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5055"
OUT = Path("/workspace/gramps-web/screens")
PASSFILE = Path("/workspace/gramps-web/.admin_password")

JS_HELPERS = r"""
(() => {
  window.__walk = function(root, acc) {
    acc = acc || [];
    const nodes = root.querySelectorAll ? [...root.querySelectorAll('*')] : [];
    for (const el of nodes) {
      acc.push(el);
      if (el.shadowRoot) window.__walk(el.shadowRoot, acc);
    }
    return acc;
  };
  window.__findId = function(id) {
    for (const el of window.__walk(document)) {
      if (el.id === id) return el;
    }
    return null;
  };
  window.__scroller = function() {
    for (const el of window.__walk(document)) {
      if (el.classList && el.classList.contains('mdc-drawer-app-content')) return el;
    }
    return null;
  };
  window.__waitChart = function() {
    for (const el of window.__walk(document)) {
      const tag = (el.tagName || '').toLowerCase();
      if (tag.includes('tree-chart') || tag.includes('fan-chart') || tag.includes('relationship-chart')) {
        const svg = el.shadowRoot ? el.shadowRoot.querySelector('svg') : el.querySelector && el.querySelector('svg');
        const r = el.getBoundingClientRect();
        if (r.width > 100 && r.height > 80) return 'ok ' + tag + ' ' + Math.round(r.width) + 'x' + Math.round(r.height);
      }
      if (tag === 'svg') {
        const r = el.getBoundingClientRect();
        if (r.width > 100 && r.height > 80) return 'svg ' + Math.round(r.width) + 'x' + Math.round(r.height);
      }
    }
    return '';
  };
  return true;
})()
"""


def load_owner():
    username = password = None
    for line in PASSFILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("username=") and username is None:
            username = line.split("=", 1)[1].strip()
        elif line.startswith("password=") and password is None and username:
            password = line.split("=", 1)[1].strip()
            break
    return username, password


def get_tokens():
    user, pw = load_owner()
    body = json.dumps({"username": user, "password": pw}).encode()
    for attempt in range(8):
        req = urllib.request.Request(
            BASE + "/api/token/", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 + attempt * 5)
                continue
            raise SystemExit(f"token failed {e.code}")
    raise SystemExit("token rate limited")


def login(page, access, refresh):
    page.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
    page.evaluate(
        """([access, refresh]) => {
            localStorage.setItem('access_token', access);
            localStorage.setItem('refresh_token', refresh);
            localStorage.setItem('access_token_expires', String(Date.now() + 900000));
        }""",
        [access, refresh],
    )


def wait_app(page, timeout_ms=20000):
    page.evaluate(JS_HELPERS)
    page.wait_for_timeout(800)
    # wait until gramps-js is present
    for _ in range(int(timeout_ms / 400)):
        ok = page.evaluate("() => !!document.querySelector('gramps-js')")
        if ok:
            return
        page.wait_for_timeout(400)


def shot_scrolled(page, section_id, path):
    page.evaluate(JS_HELPERS)
    ok = page.evaluate(
        """(ids) => {
          const toc = window.__findId(ids.toc);
          if (toc) toc.click();
          const sec = window.__findId(ids.sec);
          const sc = window.__scroller();
          if (sec && sc) {
            const srect = sec.getBoundingClientRect();
            const crect = sc.getBoundingClientRect();
            sc.scrollTop += (srect.top - crect.top - 40);
            return true;
          }
          if (sec) { sec.scrollIntoView(); return true; }
          return false;
        }""",
        {"toc": f"toc-item-{section_id}", "sec": f"section-{section_id}"},
    )
    page.wait_for_timeout(700)
    page.screenshot(path=str(path))
    return ok


def wait_chart(page, timeout_ms=25000):
    page.evaluate(JS_HELPERS)
    last = ""
    for _ in range(int(timeout_ms / 500)):
        last = page.evaluate("() => window.__waitChart && window.__waitChart()") or ""
        if last.startswith("ok") or last.startswith("svg"):
            page.wait_for_timeout(800)
            return last
        page.wait_for_timeout(500)
    return last or "timeout"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    toks = get_tokens()
    access = toks["access_token"]
    refresh = toks.get("refresh_token") or ""
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path="/usr/bin/google-chrome",
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(viewport={"width": 1500, "height": 1100})
        page = context.new_page()
        login(page, access, refresh)

        # People list
        page.goto(BASE + "/people", wait_until="networkidle", timeout=60000)
        wait_app(page)
        page.wait_for_timeout(2500)
        path = OUT / "full_people_list.png"
        page.screenshot(path=str(path))
        results["people_list"] = str(path)
        print("people_list", path, "size", path.stat().st_size)

        # Ancestor charts
        for slug, gid in (("trevor", "I0001"), ("cindy", "I0008")):
            page.goto(BASE + f"/tree/ancestor/{gid}", wait_until="networkidle", timeout=60000)
            wait_app(page)
            status = wait_chart(page, 30000)
            path = OUT / f"full_{slug}_ancestor_chart.png"
            page.screenshot(path=str(path))
            results[f"{slug}_chart"] = {"path": str(path), "status": status, "bytes": path.stat().st_size}
            print(slug, "chart", status, path, path.stat().st_size)

        # Deep ancestor person pages (top + sources/gallery)
        context = None  # keep same page
        tall = browser.new_context(viewport={"width": 1400, "height": 1800})
        page2 = tall.new_page()
        login(page2, access, refresh)
        for slug, gid in (("eli_collins", "I0768"), ("jacques_leger", "I0758")):
            page2.goto(BASE + f"/person/{gid}", wait_until="networkidle", timeout=60000)
            wait_app(page2)
            page2.wait_for_timeout(2000)
            top = OUT / f"full_{slug}.png"
            page2.screenshot(path=str(top))
            src = OUT / f"full_{slug}_sources.png"
            ok_src = shot_scrolled(page2, "sourceCitations", src)
            gal = OUT / f"full_{slug}_media.png"
            ok_gal = shot_scrolled(page2, "gallery", gal)
            results[slug] = {
                "page": str(top),
                "sources": str(src),
                "media": str(gal),
                "ok_src": ok_src,
                "ok_gal": ok_gal,
            }
            print(slug, results[slug])

        browser.close()

    (Path("/workspace/gramps-web/build/data") / "screenshot_report.json").write_text(
        json.dumps(results, indent=2)
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
