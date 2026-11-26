"""Temporary: search the built evidence index in a real browser."""
import json, subprocess, sys, time
from playwright.sync_api import sync_playwright
srv = subprocess.Popen([sys.executable, "-m", "http.server", "8765", "-d", "site"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1280, "height": 1000}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("http://localhost:8765/science-and-technology/"); pg.wait_for_timeout(800)
        print("CHECK stats:", pg.inner_text("#evstats"))
        for box, q in (("Oral", "sovereign compute"), ("Oral", "ARIA programme directors"), ("Written", "genomic data"), ("Written", "light pollution")):
            sel = f".evbox[data-type={box}]"
            pg.fill(f"{sel} input", q); pg.wait_for_timeout(2500)
            print(f"CHECK {box} '{q}':", pg.inner_text(f"{sel} .evcount"))
            items = pg.eval_on_selector_all(f"{sel} .evres li", "els=>els.slice(0,3).map(e=>[e.querySelector('a').textContent,e.querySelector('a').href,e.querySelector('.m').textContent,e.querySelector('.ex').textContent.slice(0,160)])")
            for it in items:
                print("CHECK   ", json.dumps(it, ensure_ascii=False))
        pg.select_option(".evbox[data-type=Written] select", "new"); pg.wait_for_timeout(2000)
        print("CHECK newest written:", pg.eval_on_selector_all(".evbox[data-type=Written] .evres li .m", "els=>els.slice(0,4).map(e=>e.textContent)"))
        print("CHECK errors:", errs)
        b.close()
finally:
    srv.terminate()
