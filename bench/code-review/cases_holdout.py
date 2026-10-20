"""Held-out benchmark cases for code-review skills, built exactly like ``cases.py``.

Six cases carry one seeded defect each, of classes not used in ``cases.py``; two are clean changes
that a precise reviewer should approve. The answer key lives in ``answers_holdout.json``, never in
a case. Build them with ``build.py`` by pointing it at ``CASES`` from this module.
"""

from __future__ import annotations

from cases import PYTEST_INI, Case

CASES: tuple[Case, ...] = (
    Case(
        case_id="h01-revenue-cache",
        title="Cache monthly revenue on the dashboard",
        body="The dashboard computes the month's revenue twice per page view (the total and the "
        "average order), and the SUM is the slowest query on the page for our larger accounts. "
        "This caches the result for five minutes with the TTLCache we already use for FX rates. "
        "Five minutes of staleness is fine for the dashboard.",
        test_command="python -m pytest",
        base={
            "pytest.ini": PYTEST_INI,
            "reports/__init__.py": "",
            "reports/db.py": '''import sqlite3


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS orders ("
        " id INTEGER PRIMARY KEY,"
        " tenant_id TEXT NOT NULL,"
        " month TEXT NOT NULL,"
        " amount_cents INTEGER NOT NULL)"
    )
    return conn
''',
            "reports/cache.py": '''from __future__ import annotations

import time
from typing import Any, Callable


class TTLCache:
    """In-memory key/value cache whose entries expire after ``ttl_seconds``."""

    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._entries[key] = (self._clock() + self._ttl, value)
''',
            "reports/fx.py": '''from __future__ import annotations

from decimal import Decimal
from typing import Callable

from reports.cache import TTLCache

_rates = TTLCache(ttl_seconds=3600)


def eur_rate(fetch: Callable[[str, str], Decimal], currency: str, day: str) -> Decimal:
    """ECB reference rate for ``currency`` on ``day`` (YYYY-MM-DD), cached for an hour."""
    key = f"fx:{currency}:{day}"
    rate = _rates.get(key)
    if rate is None:
        rate = fetch(currency, day)
        _rates.set(key, rate)
    return rate
''',
            "reports/service.py": '''import sqlite3


class ReportService:
    """Reporting queries behind the dashboard. Built per request from the authenticated session."""

    def __init__(self, conn: sqlite3.Connection, tenant_id: str) -> None:
        self.conn = conn
        self.tenant_id = tenant_id

    def order_count(self, month: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM orders WHERE tenant_id = ? AND month = ?",
            (self.tenant_id, month),
        ).fetchone()
        return int(row[0])

    def monthly_revenue(self, month: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM orders WHERE tenant_id = ? AND month = ?",
            (self.tenant_id, month),
        ).fetchone()
        return int(row[0])

    def average_order(self, month: str) -> int:
        count = self.order_count(month)
        return self.monthly_revenue(month) // count if count else 0
''',
            "reports/dashboard.py": '''import sqlite3

from reports.service import ReportService


def dashboard(conn: sqlite3.Connection, session: dict, month: str) -> dict:
    reports = ReportService(conn, session["tenant_id"])
    return {
        "month": month,
        "orders": reports.order_count(month),
        "revenue_cents": reports.monthly_revenue(month),
        "average_order_cents": reports.average_order(month),
    }
''',
            "tests/test_service.py": '''from reports.db import connect
from reports.service import ReportService


def _seed(conn, rows):
    conn.executemany("INSERT INTO orders (tenant_id, month, amount_cents) VALUES (?, ?, ?)", rows)
    conn.commit()


def test_revenue_only_counts_own_tenant(tmp_path):
    conn = connect(str(tmp_path / "r.db"))
    _seed(conn, [("acme", "2026-07", 1000), ("acme", "2026-07", 2500), ("globex", "2026-07", 9900)])
    assert ReportService(conn, "acme").monthly_revenue("2026-07") == 3500


def test_average_order(tmp_path):
    conn = connect(str(tmp_path / "r.db"))
    _seed(conn, [("acme", "2026-08", 1000), ("acme", "2026-08", 2000)])
    assert ReportService(conn, "acme").average_order("2026-08") == 1500


def test_empty_month(tmp_path):
    conn = connect(str(tmp_path / "r.db"))
    assert ReportService(conn, "acme").monthly_revenue("2026-01") == 0
''',
        },
        feature={
            "reports/service.py": '''import sqlite3

from reports.cache import TTLCache

_revenue_cache = TTLCache(ttl_seconds=300)


class ReportService:
    """Reporting queries behind the dashboard. Built per request from the authenticated session."""

    def __init__(self, conn: sqlite3.Connection, tenant_id: str) -> None:
        self.conn = conn
        self.tenant_id = tenant_id

    def order_count(self, month: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM orders WHERE tenant_id = ? AND month = ?",
            (self.tenant_id, month),
        ).fetchone()
        return int(row[0])

    def monthly_revenue(self, month: str) -> int:
        key = f"revenue:{month}"
        cached = _revenue_cache.get(key)
        if cached is not None:
            return cached
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM orders WHERE tenant_id = ? AND month = ?",
            (self.tenant_id, month),
        ).fetchone()
        total = int(row[0])
        _revenue_cache.set(key, total)
        return total

    def average_order(self, month: str) -> int:
        count = self.order_count(month)
        return self.monthly_revenue(month) // count if count else 0
''',
            "tests/test_revenue_cache.py": '''from reports.db import connect
from reports.service import ReportService


def test_revenue_is_served_from_cache(tmp_path):
    conn = connect(str(tmp_path / "r.db"))
    conn.execute("INSERT INTO orders (tenant_id, month, amount_cents) VALUES ('acme', '2026-09', 4200)")
    conn.commit()
    reports = ReportService(conn, "acme")
    assert reports.monthly_revenue("2026-09") == 4200
    conn.execute("DELETE FROM orders")
    conn.commit()
    assert reports.monthly_revenue("2026-09") == 4200
''',
        },
    ),
    Case(
        case_id="h02-headcount-allocation",
        title="Allocate shared invoices to teams by headcount",
        body="Finance splits shared SaaS invoices (Slack, GitHub, Figma) across teams by headcount "
        "by hand every month. bookInvoice() now takes an optional list of teams and books one "
        "expense line per team instead of a single unallocated line.",
        test_command="node --test",
        base={
            "package.json": '{\n  "name": "payables",\n  "type": "commonjs",\n  "private": true\n}\n',
            "src/ledger.js": r'''class UnbalancedJournalError extends Error {
  constructor(debits, credits) {
    super(`journal does not balance: debits ${debits} != credits ${credits}`);
    this.name = "UnbalancedJournalError";
  }
}

const posted = [];

function postJournal(journal) {
  let debits = 0;
  let credits = 0;
  for (const line of journal.lines) {
    if (!Number.isInteger(line.cents) || line.cents < 0) {
      throw new RangeError(`line ${line.account} has invalid amount ${line.cents}`);
    }
    if (line.side === "debit") {
      debits += line.cents;
    } else if (line.side === "credit") {
      credits += line.cents;
    } else {
      throw new RangeError(`unknown side ${line.side}`);
    }
  }
  if (debits !== credits) {
    throw new UnbalancedJournalError(debits, credits);
  }
  posted.push(journal);
  return posted.length;
}

module.exports = { postJournal, posted, UnbalancedJournalError };
''',
            "src/payables.js": r'''const { postJournal } = require("./ledger");

function bookInvoice(invoice) {
  return postJournal({
    ref: invoice.id,
    lines: [
      { account: "expenses:unallocated", side: "debit", cents: invoice.totalCents },
      { account: "liabilities:payables", side: "credit", cents: invoice.totalCents },
    ],
  });
}

module.exports = { bookInvoice };
''',
            "test/payables.test.js": r'''const test = require("node:test");
const assert = require("node:assert");
const { bookInvoice } = require("../src/payables");
const { posted, postJournal, UnbalancedJournalError } = require("../src/ledger");

test("books an invoice as payable", () => {
  const before = posted.length;
  bookInvoice({ id: "inv_1", totalCents: 4999 });
  assert.strictEqual(posted.length, before + 1);
});

test("ledger rejects unbalanced journals", () => {
  assert.throws(
    () => postJournal({ ref: "x", lines: [{ account: "a", side: "debit", cents: 1 }] }),
    UnbalancedJournalError,
  );
});
''',
        },
        feature={
            "src/allocation.js": r'''function splitByHeadcount(totalCents, teams) {
  const headcount = teams.reduce((sum, team) => sum + team.headcount, 0);
  if (headcount === 0) {
    throw new RangeError("cannot split an invoice across teams with no people");
  }
  return teams.map((team) => ({
    team: team.id,
    cents: Math.round((totalCents * team.headcount) / headcount),
  }));
}

module.exports = { splitByHeadcount };
''',
            "src/payables.js": r'''const { postJournal } = require("./ledger");
const { splitByHeadcount } = require("./allocation");

function bookInvoice(invoice, teams = []) {
  const debits =
    teams.length > 0
      ? splitByHeadcount(invoice.totalCents, teams).map((share) => ({
          account: `expenses:${share.team}`,
          side: "debit",
          cents: share.cents,
        }))
      : [{ account: "expenses:unallocated", side: "debit", cents: invoice.totalCents }];
  return postJournal({
    ref: invoice.id,
    lines: [...debits, { account: "liabilities:payables", side: "credit", cents: invoice.totalCents }],
  });
}

module.exports = { bookInvoice };
''',
            "test/allocation.test.js": r'''const test = require("node:test");
const assert = require("node:assert");
const { splitByHeadcount } = require("../src/allocation");
const { bookInvoice } = require("../src/payables");
const { posted } = require("../src/ledger");

test("splits a shared invoice by headcount", () => {
  const shares = splitByHeadcount(90000, [
    { id: "eng", headcount: 6 },
    { id: "sales", headcount: 3 },
  ]);
  assert.deepStrictEqual(shares, [
    { team: "eng", cents: 60000 },
    { team: "sales", cents: 30000 },
  ]);
});

test("teams with nobody get nothing", () => {
  const shares = splitByHeadcount(5000, [
    { id: "eng", headcount: 4 },
    { id: "design", headcount: 0 },
  ]);
  assert.deepStrictEqual(shares, [
    { team: "eng", cents: 5000 },
    { team: "design", cents: 0 },
  ]);
});

test("rejects a split with no people", () => {
  assert.throws(() => splitByHeadcount(5000, [{ id: "eng", headcount: 0 }]), RangeError);
});

test("allocated invoice books one expense line per team", () => {
  const before = posted.length;
  bookInvoice({ id: "inv_77", totalCents: 12000 }, [
    { id: "eng", headcount: 3 },
    { id: "ops", headcount: 1 },
  ]);
  const journal = posted[before];
  assert.deepStrictEqual(
    journal.lines.map((line) => [line.account, line.cents]),
    [
      ["expenses:eng", 9000],
      ["expenses:ops", 3000],
      ["liabilities:payables", 12000],
    ],
  );
});
''',
        },
    ),
    Case(
        case_id="h03-price-list-import",
        title="Import supplier price lists from CSV",
        body="Merchandising uploads one CSV per price list. import_price_list() parses it and "
        "replaces the whole list in a single transaction, so a bad file leaves the previous prices "
        "in place (there is a test for that).",
        test_command="python -m pytest",
        base={
            "pytest.ini": PYTEST_INI,
            "catalog/__init__.py": "",
            "catalog/pool.py": '''import sqlite3
import threading


class PoolExhausted(RuntimeError):
    pass


class ConnectionPool:
    """A fixed number of SQLite connections shared by the request threads."""

    def __init__(self, path: str, size: int = 4, timeout: float = 2.0) -> None:
        self._path = path
        self._timeout = timeout
        self._idle: list[sqlite3.Connection] = []
        self._slots = threading.BoundedSemaphore(size)
        self._lock = threading.Lock()

    def acquire(self) -> sqlite3.Connection:
        if not self._slots.acquire(timeout=self._timeout):
            raise PoolExhausted("no database connection available")
        with self._lock:
            if self._idle:
                return self._idle.pop()
        return sqlite3.connect(self._path, check_same_thread=False)

    def release(self, conn: sqlite3.Connection) -> None:
        with self._lock:
            self._idle.append(conn)
        self._slots.release()
''',
            "catalog/schema.py": '''from catalog.pool import ConnectionPool

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS prices ("
    " list_id TEXT NOT NULL,"
    " sku TEXT NOT NULL,"
    " cents INTEGER NOT NULL CHECK (cents >= 0),"
    " PRIMARY KEY (list_id, sku))"
)


def init_schema(pool: ConnectionPool) -> None:
    conn = pool.acquire()
    try:
        conn.execute(SCHEMA)
        conn.commit()
    finally:
        pool.release(conn)
''',
            "catalog/prices.py": '''from __future__ import annotations

from catalog.pool import ConnectionPool


def get_price(pool: ConnectionPool, list_id: str, sku: str) -> int | None:
    conn = pool.acquire()
    try:
        row = conn.execute(
            "SELECT cents FROM prices WHERE list_id = ? AND sku = ?", (list_id, sku)
        ).fetchone()
    finally:
        pool.release(conn)
    return None if row is None else int(row[0])


def set_price(pool: ConnectionPool, list_id: str, sku: str, cents: int) -> None:
    conn = pool.acquire()
    try:
        with conn:
            conn.execute(
                "INSERT INTO prices (list_id, sku, cents) VALUES (?, ?, ?)"
                " ON CONFLICT (list_id, sku) DO UPDATE SET cents = excluded.cents",
                (list_id, sku, cents),
            )
    finally:
        pool.release(conn)
''',
            "tests/test_prices.py": '''import pytest

from catalog.pool import ConnectionPool
from catalog.prices import get_price, set_price
from catalog.schema import init_schema


@pytest.fixture
def pool(tmp_path):
    pool = ConnectionPool(str(tmp_path / "catalog.db"), size=4)
    init_schema(pool)
    return pool


def test_set_then_get(pool):
    set_price(pool, "eu", "A-1", 1250)
    assert get_price(pool, "eu", "A-1") == 1250


def test_set_overwrites(pool):
    set_price(pool, "eu", "A-1", 1250)
    set_price(pool, "eu", "A-1", 990)
    assert get_price(pool, "eu", "A-1") == 990


def test_missing_price(pool):
    assert get_price(pool, "eu", "nope") is None
''',
        },
        feature={
            "catalog/prices.py": '''from __future__ import annotations

from collections.abc import Iterable

from catalog.pool import ConnectionPool


def get_price(pool: ConnectionPool, list_id: str, sku: str) -> int | None:
    conn = pool.acquire()
    try:
        row = conn.execute(
            "SELECT cents FROM prices WHERE list_id = ? AND sku = ?", (list_id, sku)
        ).fetchone()
    finally:
        pool.release(conn)
    return None if row is None else int(row[0])


def set_price(pool: ConnectionPool, list_id: str, sku: str, cents: int) -> None:
    conn = pool.acquire()
    try:
        with conn:
            conn.execute(
                "INSERT INTO prices (list_id, sku, cents) VALUES (?, ?, ?)"
                " ON CONFLICT (list_id, sku) DO UPDATE SET cents = excluded.cents",
                (list_id, sku, cents),
            )
    finally:
        pool.release(conn)


def replace_price_list(pool: ConnectionPool, list_id: str, rows: Iterable[tuple[str, int]]) -> int:
    """Atomically replace every price in ``list_id``; returns how many prices were written."""
    conn = pool.acquire()
    count = 0
    with conn:
        conn.execute("DELETE FROM prices WHERE list_id = ?", (list_id,))
        for sku, cents in rows:
            conn.execute(
                "INSERT INTO prices (list_id, sku, cents) VALUES (?, ?, ?)", (list_id, sku, cents)
            )
            count += 1
    pool.release(conn)
    return count
''',
            "catalog/importer.py": '''from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal

from catalog.pool import ConnectionPool
from catalog.prices import replace_price_list


def parse_rows(text: str) -> Iterator[tuple[str, int]]:
    """Yield (sku, cents) from a CSV with ``sku`` and ``price`` columns, price in euros."""
    for record in csv.DictReader(io.StringIO(text)):
        cents = (Decimal(record["price"]) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        yield record["sku"].strip(), int(cents)


def import_price_list(pool: ConnectionPool, list_id: str, text: str) -> int:
    return replace_price_list(pool, list_id, parse_rows(text))
''',
            "tests/test_importer.py": r'''import sqlite3

import pytest

from catalog.importer import import_price_list
from catalog.pool import ConnectionPool
from catalog.prices import get_price, set_price
from catalog.schema import init_schema


@pytest.fixture
def pool(tmp_path):
    pool = ConnectionPool(str(tmp_path / "catalog.db"), size=4)
    init_schema(pool)
    return pool


def test_import_replaces_the_list(pool):
    set_price(pool, "eu", "OLD-1", 999)
    count = import_price_list(pool, "eu", "sku,price\nA-1,12.50\nB-2,3\n")
    assert count == 2
    assert get_price(pool, "eu", "A-1") == 1250
    assert get_price(pool, "eu", "B-2") == 300
    assert get_price(pool, "eu", "OLD-1") is None


def test_import_leaves_other_lists_alone(pool):
    set_price(pool, "us", "A-1", 1100)
    import_price_list(pool, "eu", "sku,price\nA-1,12.50\n")
    assert get_price(pool, "us", "A-1") == 1100


def test_bad_row_keeps_previous_prices(pool):
    set_price(pool, "eu", "A-1", 1000)
    with pytest.raises(sqlite3.IntegrityError):
        import_price_list(pool, "eu", "sku,price\nA-1,12.50\nB-2,-1\n")
    assert get_price(pool, "eu", "A-1") == 1000
''',
        },
    ),
    Case(
        case_id="h04-login-return-to",
        title="Send users back to where they were after login",
        body="Login always landed on /dashboard, so deep links from emails and from the docs site "
        "lost their target. completeLogin() now honours a returnTo query parameter: same-site "
        "paths are kept, links to our other subdomains (docs, status, billing) are allowed, and "
        "anything else falls back to /dashboard.",
        test_command="node --test",
        base={
            "package.json": '{\n  "name": "auth-web",\n  "type": "commonjs",\n  "private": true\n}\n',
            "src/config.js": r'''module.exports = {
  appOrigin: "https://app.example.com",
  defaultReturn: "/dashboard",
};
''',
            "src/session.js": r'''const crypto = require("node:crypto");

const sessions = new Map();

function createSession(userId) {
  const session = { id: crypto.randomBytes(24).toString("base64url"), userId, createdAt: Date.now() };
  sessions.set(session.id, session);
  return session;
}

module.exports = { createSession, sessions };
''',
            "src/login.js": r'''const { defaultReturn } = require("./config");
const { createSession } = require("./session");

function completeLogin(user) {
  const session = createSession(user.id);
  return {
    status: 302,
    headers: {
      location: defaultReturn,
      "set-cookie": `sid=${session.id}; HttpOnly; Secure; SameSite=Lax; Path=/`,
    },
  };
}

module.exports = { completeLogin };
''',
            "test/login.test.js": r'''const test = require("node:test");
const assert = require("node:assert");
const { completeLogin } = require("../src/login");

test("login sets a session cookie and redirects to the dashboard", () => {
  const res = completeLogin({ id: "u_1" });
  assert.strictEqual(res.status, 302);
  assert.strictEqual(res.headers.location, "/dashboard");
  assert.match(res.headers["set-cookie"], /^sid=[\w-]+; HttpOnly; Secure/);
});
''',
        },
        feature={
            "src/returnTo.js": r'''const { appOrigin, defaultReturn } = require("./config");

const TRUSTED_ORIGIN = /^https:\/\/([a-z0-9-]+\.)*example\.com/;

function safeReturnTo(target) {
  if (typeof target !== "string" || target.length === 0 || target.length > 2048) {
    return defaultReturn;
  }
  let url;
  try {
    url = new URL(target, appOrigin);
  } catch {
    return defaultReturn;
  }
  if (url.origin === appOrigin) {
    return `${url.pathname}${url.search}${url.hash}`;
  }
  return TRUSTED_ORIGIN.test(url.href) ? url.href : defaultReturn;
}

module.exports = { safeReturnTo };
''',
            "src/login.js": r'''const { createSession } = require("./session");
const { safeReturnTo } = require("./returnTo");

function completeLogin(user, query = {}) {
  const session = createSession(user.id);
  return {
    status: 302,
    headers: {
      location: safeReturnTo(query.returnTo),
      "set-cookie": `sid=${session.id}; HttpOnly; Secure; SameSite=Lax; Path=/`,
    },
  };
}

module.exports = { completeLogin };
''',
            "test/returnTo.test.js": r'''const test = require("node:test");
const assert = require("node:assert");
const { safeReturnTo } = require("../src/returnTo");
const { completeLogin } = require("../src/login");

test("keeps a same-site path", () => {
  assert.strictEqual(safeReturnTo("/billing/invoices?page=2#latest"), "/billing/invoices?page=2#latest");
});

test("same-site absolute URLs become paths", () => {
  assert.strictEqual(safeReturnTo("https://app.example.com/settings"), "/settings");
});

test("allows our other subdomains", () => {
  assert.strictEqual(safeReturnTo("https://docs.example.com/guides/sso"), "https://docs.example.com/guides/sso");
});

test("falls back to the dashboard for anything else", () => {
  for (const target of [
    undefined,
    "",
    "https://evil.test/login",
    "//evil.test/login",
    "/\\evil.test/login",
    "javascript:alert(1)",
    "http://docs.example.com/guides",
  ]) {
    assert.strictEqual(safeReturnTo(target), "/dashboard", String(target));
  }
});

test("login redirects to returnTo", () => {
  const res = completeLogin({ id: "u_1" }, { returnTo: "/settings/profile" });
  assert.strictEqual(res.headers.location, "/settings/profile");
});
''',
        },
    ),
    Case(
        case_id="h05-usage-billing",
        title="Bill API usage on monthly invoices",
        body="Usage-based pricing goes live in November. build_invoice() now takes the account's "
        "usage events and adds an 'API usage' line at EUR 0.40 per 1,000 calls, rounding partial "
        "thousands up. Events are bucketed by their UTC date.",
        test_command="python -m pytest",
        base={
            "pytest.ini": PYTEST_INI,
            "billing/__init__.py": "",
            "billing/periods.py": '''from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class BillingPeriod:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def label(self) -> str:
        return f"{self.start:%d %b %Y} - {self.end:%d %b %Y}"


def month_period(year: int, month: int) -> BillingPeriod:
    last_day = calendar.monthrange(year, month)[1]
    return BillingPeriod(date(year, month, 1), date(year, month, last_day))
''',
            "billing/accounts.py": '''from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Account:
    id: str
    plan: str
    monthly_cents: int
    active_from: date
''',
            "billing/subscription.py": '''from __future__ import annotations

from datetime import date

from billing.periods import BillingPeriod


def prorated_cents(monthly_cents: int, period: BillingPeriod, active_from: date) -> int:
    """Charge for the days from ``active_from`` to the end of ``period``."""
    if active_from <= period.start:
        return monthly_cents
    if active_from > period.end:
        return 0
    active_days = (period.end - active_from).days + 1
    return monthly_cents * active_days // period.days
''',
            "billing/invoice.py": '''from __future__ import annotations

from billing.accounts import Account
from billing.periods import BillingPeriod
from billing.subscription import prorated_cents


def build_invoice(account: Account, period: BillingPeriod) -> dict:
    lines = [
        {
            "description": f"{account.plan.title()} plan",
            "cents": prorated_cents(account.monthly_cents, period, account.active_from),
        }
    ]
    return {
        "account_id": account.id,
        "period": period.label(),
        "lines": lines,
        "total_cents": sum(line["cents"] for line in lines),
    }
''',
            "tests/test_invoice.py": '''from datetime import date

from billing.accounts import Account
from billing.invoice import build_invoice
from billing.periods import month_period


def test_full_month():
    account = Account("acc_1", "team", 4900, date(2026, 1, 15))
    invoice = build_invoice(account, month_period(2026, 10))
    assert invoice["total_cents"] == 4900
    assert invoice["period"] == "01 Oct 2026 - 31 Oct 2026"


def test_prorated_first_month():
    account = Account("acc_1", "team", 3100, date(2026, 10, 22))
    invoice = build_invoice(account, month_period(2026, 10))
    assert invoice["total_cents"] == 1000
''',
        },
        feature={
            "billing/usage.py": '''from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from billing.periods import BillingPeriod

API_CALL_PRICE_CENTS_PER_1000 = 40


@dataclass(frozen=True)
class UsageEvent:
    account_id: str
    occurred_at: datetime
    api_calls: int


def billable_calls(events: Iterable[UsageEvent], account_id: str, period: BillingPeriod) -> int:
    return sum(
        event.api_calls
        for event in events
        if event.account_id == account_id and period.start <= event.occurred_at.date() < period.end
    )


def usage_cents(calls: int) -> int:
    return (calls * API_CALL_PRICE_CENTS_PER_1000 + 999) // 1000
''',
            "billing/invoice.py": '''from __future__ import annotations

from collections.abc import Iterable

from billing.accounts import Account
from billing.periods import BillingPeriod
from billing.subscription import prorated_cents
from billing.usage import UsageEvent, billable_calls, usage_cents


def build_invoice(account: Account, period: BillingPeriod, usage: Iterable[UsageEvent] = ()) -> dict:
    lines = [
        {
            "description": f"{account.plan.title()} plan",
            "cents": prorated_cents(account.monthly_cents, period, account.active_from),
        }
    ]
    calls = billable_calls(usage, account.id, period)
    if calls:
        lines.append({"description": f"API usage ({calls:,} calls)", "cents": usage_cents(calls)})
    return {
        "account_id": account.id,
        "period": period.label(),
        "lines": lines,
        "total_cents": sum(line["cents"] for line in lines),
    }
''',
            "tests/test_usage.py": '''from datetime import date, datetime, timezone

from billing.accounts import Account
from billing.invoice import build_invoice
from billing.periods import month_period
from billing.usage import UsageEvent, billable_calls, usage_cents

UTC = timezone.utc


def test_counts_only_this_accounts_events_in_the_period():
    events = [
        UsageEvent("acc_1", datetime(2026, 9, 30, 23, 59, tzinfo=UTC), 5000),
        UsageEvent("acc_1", datetime(2026, 10, 1, 0, 0, tzinfo=UTC), 1000),
        UsageEvent("acc_1", datetime(2026, 10, 17, 12, 30, tzinfo=UTC), 2500),
        UsageEvent("acc_2", datetime(2026, 10, 17, 12, 30, tzinfo=UTC), 9999),
        UsageEvent("acc_1", datetime(2026, 11, 1, 0, 0, tzinfo=UTC), 7000),
    ]
    assert billable_calls(events, "acc_1", month_period(2026, 10)) == 3500


def test_partial_thousands_round_up():
    assert usage_cents(0) == 0
    assert usage_cents(1) == 1
    assert usage_cents(2500) == 100
    assert usage_cents(2501) == 101


def test_invoice_adds_a_usage_line():
    account = Account("acc_1", "team", 4900, date(2026, 1, 15))
    usage = [UsageEvent("acc_1", datetime(2026, 10, 9, 8, 0, tzinfo=UTC), 12_000)]
    invoice = build_invoice(account, month_period(2026, 10), usage)
    assert invoice["lines"][-1] == {"description": "API usage (12,000 calls)", "cents": 480}
    assert invoice["total_cents"] == 5380


def test_no_usage_no_line():
    account = Account("acc_1", "team", 4900, date(2026, 1, 15))
    assert len(build_invoice(account, month_period(2026, 10))["lines"]) == 1
''',
        },
    ),
    Case(
        case_id="h06-flag-env-override",
        title="Let on-call override flag rollouts from the environment",
        body="Changing a rollout percentage today means editing flagConfig.js and shipping a "
        "release. This reads FLAG_<NAME>_PERCENT from the environment and uses it instead of the "
        "configured percentage, so on-call can dial a rollout up or down with a restart. Values "
        "above 100 are clamped and anything that is not a number is ignored, so a typo cannot "
        "break a flag.",
        test_command="node --test",
        base={
            "package.json": '{\n  "name": "flags",\n  "type": "commonjs",\n  "private": true\n}\n',
            "src/flagConfig.js": r'''module.exports = {
  "new-checkout": { rolloutPercent: 25 },
  "invoice-v2": { rolloutPercent: 100 },
  "bulk-export": { rolloutPercent: 50 },
};
''',
            "src/flags.js": r'''const crypto = require("node:crypto");
const defaultConfig = require("./flagConfig");

function bucket(flagName, userId) {
  const digest = crypto.createHash("sha256").update(`${flagName}:${userId}`).digest();
  return digest.readUInt32BE(0) % 100;
}

function createFlags(config) {
  return {
    isEnabled(flagName, userId) {
      const flag = config[flagName];
      if (!flag || typeof flag.rolloutPercent !== "number") {
        return false;
      }
      return bucket(flagName, userId) < flag.rolloutPercent;
    },
  };
}

const flags = createFlags(defaultConfig);

module.exports = { createFlags, bucket, isEnabled: flags.isEnabled };
''',
            "test/flags.test.js": r'''const test = require("node:test");
const assert = require("node:assert");
const { createFlags, bucket } = require("../src/flags");

const users = Array.from({ length: 1000 }, (_, i) => `user-${i}`);
const share = (flags, name) => users.filter((u) => flags.isEnabled(name, u)).length / users.length;

test("unknown flags are off", () => {
  assert.strictEqual(createFlags({}).isEnabled("nope", "user-1"), false);
});

test("rollout percentage is roughly respected", () => {
  const s = share(createFlags({ beta: { rolloutPercent: 30 } }), "beta");
  assert.ok(s > 0.25 && s < 0.35, `got ${s}`);
});

test("buckets are stable per user", () => {
  assert.strictEqual(bucket("beta", "user-7"), bucket("beta", "user-7"));
});
''',
        },
        feature={
            "src/flags.js": r'''const crypto = require("node:crypto");
const defaultConfig = require("./flagConfig");

function bucket(flagName, userId) {
  const digest = crypto.createHash("sha256").update(`${flagName}:${userId}`).digest();
  return digest.readUInt32BE(0) % 100;
}

function overrideVariable(flagName) {
  return `FLAG_${flagName.toUpperCase().replace(/-/g, "_")}_PERCENT`;
}

function effectivePercent(flagName, flag, env) {
  const override = Number(env[overrideVariable(flagName)]);
  const percent = override || flag.rolloutPercent;
  return Math.min(100, Math.max(0, percent));
}

function createFlags(config, env = process.env) {
  return {
    isEnabled(flagName, userId) {
      const flag = config[flagName];
      if (!flag || typeof flag.rolloutPercent !== "number") {
        return false;
      }
      return bucket(flagName, userId) < effectivePercent(flagName, flag, env);
    },
  };
}

const flags = createFlags(defaultConfig);

module.exports = { createFlags, bucket, overrideVariable, isEnabled: flags.isEnabled };
''',
            "test/overrides.test.js": r'''const test = require("node:test");
const assert = require("node:assert");
const { createFlags, overrideVariable } = require("../src/flags");

const users = Array.from({ length: 1000 }, (_, i) => `user-${i}`);
const share = (flags, name) => users.filter((u) => flags.isEnabled(name, u)).length / users.length;

test("variable name is derived from the flag name", () => {
  assert.strictEqual(overrideVariable("new-checkout"), "FLAG_NEW_CHECKOUT_PERCENT");
});

test("environment raises a rollout", () => {
  const flags = createFlags({ "new-checkout": { rolloutPercent: 10 } }, { FLAG_NEW_CHECKOUT_PERCENT: "100" });
  assert.strictEqual(share(flags, "new-checkout"), 1);
});

test("environment lowers a rollout", () => {
  const flags = createFlags({ "new-checkout": { rolloutPercent: 100 } }, { FLAG_NEW_CHECKOUT_PERCENT: "20" });
  const s = share(flags, "new-checkout");
  assert.ok(s > 0.15 && s < 0.25, `got ${s}`);
});

test("non-numeric values are ignored", () => {
  const flags = createFlags({ "new-checkout": { rolloutPercent: 100 } }, { FLAG_NEW_CHECKOUT_PERCENT: "fifty" });
  assert.strictEqual(share(flags, "new-checkout"), 1);
});

test("values above 100 are clamped", () => {
  const flags = createFlags({ "new-checkout": { rolloutPercent: 50 } }, { FLAG_NEW_CHECKOUT_PERCENT: "250" });
  assert.strictEqual(share(flags, "new-checkout"), 1);
});

test("without a variable the configured percentage applies", () => {
  const s = share(createFlags({ "new-checkout": { rolloutPercent: 30 } }, {}), "new-checkout");
  assert.ok(s > 0.25 && s < 0.35, `got ${s}`);
});
''',
        },
    ),
    Case(
        case_id="h07-clean-webhook-retries",
        title="Retry failed webhook deliveries with backoff",
        body="Endpoints that blip for a few seconds lose events today: deliver() makes one attempt "
        "and the event is marked failed. It now retries timeouts, 408, 425, 429 and 5xx up to 5 "
        "attempts with capped exponential backoff and jitter, and gives up at once on any other "
        "status. Receivers already deduplicate on Webhook-Id, so a retry is safe. deliver() "
        "returns a DeliveryResult so the dispatcher can log how many attempts it took.",
        test_command="python -m pytest",
        base={
            "pytest.ini": PYTEST_INI,
            "docs/webhooks.md": """# Receiving webhooks

We POST a JSON body to your endpoint for every event you subscribed to.

- Respond with any 2xx status once you have stored the event.
- Deliveries are at-least-once: the same event can arrive more than once.
  Every request carries a `Webhook-Id` header with the event id; ignore ids
  you have already processed.
""",
            "webhooks/__init__.py": "",
            "webhooks/models.py": '''from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Event:
    id: str
    type: str
    payload: dict[str, Any]
''',
            "webhooks/transport.py": '''class Transport:
    """Sends one HTTP POST and returns the response status code.

    Raises ConnectionError (timeouts included) when no response arrives.
    """

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        raise NotImplementedError
''',
            "webhooks/outbox.py": '''from __future__ import annotations

from webhooks.models import Event


class Outbox:
    """In-memory outbox; production uses the Postgres-backed one with the same interface."""

    def __init__(self) -> None:
        self._pending: list[tuple[str, Event]] = []
        self.delivered: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str]] = []

    def enqueue(self, endpoint: str, event: Event) -> None:
        self._pending.append((endpoint, event))

    def pending(self) -> list[tuple[str, Event]]:
        batch, self._pending = self._pending, []
        return batch

    def mark_delivered(self, event_id: str, endpoint: str) -> None:
        self.delivered.append((event_id, endpoint))

    def mark_failed(self, event_id: str, endpoint: str) -> None:
        self.failed.append((event_id, endpoint))
''',
            "webhooks/delivery.py": '''"""Outgoing webhook delivery.

Every request carries the event id in the Webhook-Id header; receivers deduplicate on it
(see docs/webhooks.md).
"""

import json

from webhooks.models import Event
from webhooks.transport import Transport


def encode(event: Event) -> bytes:
    document = {"id": event.id, "type": event.type, "data": event.payload}
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


def deliver(transport: Transport, endpoint: str, event: Event) -> bool:
    headers = {"Content-Type": "application/json", "Webhook-Id": event.id}
    try:
        status = transport.post(endpoint, encode(event), headers)
    except ConnectionError:
        return False
    return 200 <= status < 300
''',
            "webhooks/dispatcher.py": '''from webhooks.delivery import deliver
from webhooks.outbox import Outbox
from webhooks.transport import Transport


def dispatch_pending(outbox: Outbox, transport: Transport) -> int:
    """Deliver every queued event; returns how many were delivered.

    Runs in the background worker, never inside a web request.
    """
    delivered = 0
    for endpoint, event in outbox.pending():
        if deliver(transport, endpoint, event):
            outbox.mark_delivered(event.id, endpoint)
            delivered += 1
        else:
            outbox.mark_failed(event.id, endpoint)
    return delivered
''',
            "tests/test_delivery.py": '''from webhooks.delivery import deliver
from webhooks.dispatcher import dispatch_pending
from webhooks.models import Event
from webhooks.outbox import Outbox
from webhooks.transport import Transport

EVENT = Event("evt_1", "invoice.paid", {"invoice": "inv_1", "amount": 1200})


class FakeTransport(Transport):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, body, headers):
        self.calls.append((url, body, dict(headers)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_delivered_on_2xx():
    assert deliver(FakeTransport([204]), "https://hooks.example.test/in", EVENT) is True


def test_failed_on_error_status():
    assert deliver(FakeTransport([500]), "https://hooks.example.test/in", EVENT) is False


def test_dispatch_marks_outcomes():
    outbox = Outbox()
    outbox.enqueue("https://a.example.test", EVENT)
    outbox.enqueue("https://b.example.test", EVENT)
    assert dispatch_pending(outbox, FakeTransport([200, 404])) == 1
    assert outbox.delivered == [("evt_1", "https://a.example.test")]
    assert outbox.failed == [("evt_1", "https://b.example.test")]
''',
        },
        feature={
            "webhooks/delivery.py": '''"""Outgoing webhook delivery.

Every request carries the event id in the Webhook-Id header; receivers deduplicate on it
(see docs/webhooks.md), so the same delivery can safely be attempted more than once.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from webhooks.models import Event
from webhooks.transport import Transport

RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    attempts: int
    status: int | None


def encode(event: Event) -> bytes:
    document = {"id": event.id, "type": event.type, "data": event.payload}
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


def backoff_delay(attempt: int, base_delay: float, max_delay: float, jitter: float) -> float:
    """Seconds to wait after failed attempt ``attempt`` (1-based); ``jitter`` is in [0, 1)."""
    return min(max_delay, base_delay * 2 ** (attempt - 1)) * (0.5 + jitter / 2)


def deliver(
    transport: Transport,
    endpoint: str,
    event: Event,
    *,
    max_attempts: int = 5,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> DeliveryResult:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    body = encode(event)
    headers = {"Content-Type": "application/json", "Webhook-Id": event.id}
    status: int | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            status = transport.post(endpoint, body, headers)
        except ConnectionError:
            status = None
        else:
            if 200 <= status < 300:
                return DeliveryResult(True, attempt, status)
            if status not in RETRYABLE_STATUSES:
                return DeliveryResult(False, attempt, status)
        if attempt < max_attempts:
            sleep(backoff_delay(attempt, base_delay, max_delay, jitter()))
    return DeliveryResult(False, max_attempts, status)
''',
            "webhooks/dispatcher.py": '''import logging

from webhooks.delivery import deliver
from webhooks.outbox import Outbox
from webhooks.transport import Transport

log = logging.getLogger(__name__)


def dispatch_pending(outbox: Outbox, transport: Transport) -> int:
    """Deliver every queued event; returns how many were delivered.

    Runs in the background worker, never inside a web request.
    """
    delivered = 0
    for endpoint, event in outbox.pending():
        result = deliver(transport, endpoint, event)
        if result.delivered:
            outbox.mark_delivered(event.id, endpoint)
            delivered += 1
        else:
            log.warning(
                "webhook %s to %s failed after %d attempt(s), last status %s",
                event.id,
                endpoint,
                result.attempts,
                result.status,
            )
            outbox.mark_failed(event.id, endpoint)
    return delivered
''',
            "tests/test_delivery.py": '''import pytest

from webhooks.delivery import backoff_delay, deliver
from webhooks.dispatcher import dispatch_pending
from webhooks.models import Event
from webhooks.outbox import Outbox
from webhooks.transport import Transport

EVENT = Event("evt_1", "invoice.paid", {"invoice": "inv_1", "amount": 1200})
URL = "https://hooks.example.test/in"


class FakeTransport(Transport):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, body, headers):
        self.calls.append((url, body, dict(headers)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def run(responses, **kwargs):
    transport = FakeTransport(responses)
    sleeps = []
    result = deliver(transport, URL, EVENT, sleep=sleeps.append, jitter=lambda: 1.0, **kwargs)
    return result, transport, sleeps


def test_first_attempt_succeeds():
    result, transport, sleeps = run([204])
    assert (result.delivered, result.attempts, result.status) == (True, 1, 204)
    assert sleeps == []


def test_retries_server_errors_with_growing_delays():
    result, _, sleeps = run([503, 502, 200])
    assert (result.delivered, result.attempts) == (True, 3)
    assert sleeps == [0.5, 1.0]


def test_retries_connection_errors():
    result, _, _ = run([ConnectionError("reset"), 200])
    assert (result.delivered, result.attempts) == (True, 2)


def test_does_not_retry_client_errors():
    result, transport, sleeps = run([400])
    assert (result.delivered, result.attempts, result.status) == (False, 1, 400)
    assert len(transport.calls) == 1
    assert sleeps == []


def test_gives_up_after_max_attempts():
    result, transport, sleeps = run([500] * 5)
    assert (result.delivered, result.attempts, result.status) == (False, 5, 500)
    assert len(transport.calls) == 5
    assert sleeps == [0.5, 1.0, 2.0, 4.0]


def test_last_status_is_none_when_endpoint_unreachable():
    result, _, _ = run([ConnectionError("down")] * 2, max_attempts=2)
    assert (result.delivered, result.attempts, result.status) == (False, 2, None)


def test_every_attempt_sends_the_same_request():
    _, transport, _ = run([503, 200])
    first, second = transport.calls
    assert first == second
    assert first[2]["Webhook-Id"] == "evt_1"


def test_backoff_is_capped_and_jittered():
    assert backoff_delay(10, 0.5, 30.0, 1.0) == 30.0
    assert backoff_delay(10, 0.5, 30.0, 0.0) == 15.0
    assert backoff_delay(1, 0.5, 30.0, 0.0) == 0.25


def test_rejects_zero_attempts():
    with pytest.raises(ValueError):
        deliver(FakeTransport([]), URL, EVENT, max_attempts=0)


def test_dispatch_marks_outcomes():
    outbox = Outbox()
    outbox.enqueue("https://a.example.test", EVENT)
    outbox.enqueue("https://b.example.test", EVENT)
    assert dispatch_pending(outbox, FakeTransport([200, 404])) == 1
    assert outbox.delivered == [("evt_1", "https://a.example.test")]
    assert outbox.failed == [("evt_1", "https://b.example.test")]
''',
        },
    ),
    Case(
        case_id="h08-clean-thumbnail-concurrency",
        title="Generate thumbnails with bounded concurrency",
        body="Gallery uploads of 50+ photos took minutes because thumbnails were resized one at a "
        "time. Adds a small mapLimit() helper and resizes up to 4 images at once. Output order and "
        "the existing generateThumbnails(paths, size) signature are unchanged.",
        test_command="node --test",
        base={
            "package.json": '{\n  "name": "gallery",\n  "type": "commonjs",\n  "private": true\n}\n',
            "src/image.js": r'''const SUPPORTED = new Set([".jpg", ".jpeg", ".png", ".webp"]);

function extension(path) {
  const dot = path.lastIndexOf(".");
  return dot === -1 ? "" : path.slice(dot).toLowerCase();
}

async function resize(path, size) {
  const ext = extension(path);
  if (!SUPPORTED.has(ext)) {
    throw new Error(`unsupported image type: ${path}`);
  }
  await new Promise((resolve) => setTimeout(resolve, 5));
  return { source: path, thumbnail: `${path.slice(0, -ext.length)}.${size}w${ext}`, size };
}

module.exports = { resize };
''',
            "src/thumbnails.js": r'''const { resize } = require("./image");

async function generateThumbnails(paths, size = 256) {
  const thumbnails = [];
  for (const path of paths) {
    thumbnails.push(await resize(path, size));
  }
  return thumbnails;
}

module.exports = { generateThumbnails };
''',
            "src/upload.js": r'''const { generateThumbnails } = require("./thumbnails");

async function handleGalleryUpload(album, paths) {
  const thumbnails = await generateThumbnails(paths, 320);
  return {
    album,
    items: thumbnails.map((t) => ({ src: t.source, thumb: t.thumbnail })),
  };
}

module.exports = { handleGalleryUpload };
''',
            "test/thumbnails.test.js": r'''const test = require("node:test");
const assert = require("node:assert");
const { generateThumbnails } = require("../src/thumbnails");
const { handleGalleryUpload } = require("../src/upload");

test("thumbnails keep the order of the uploaded files", async () => {
  const thumbs = await generateThumbnails(["a.jpg", "b.png", "c.webp"], 128);
  assert.deepStrictEqual(
    thumbs.map((t) => t.thumbnail),
    ["a.128w.jpg", "b.128w.png", "c.128w.webp"],
  );
});

test("unsupported files fail the upload", async () => {
  await assert.rejects(generateThumbnails(["a.jpg", "notes.txt"]), /unsupported image type/);
});

test("gallery uploads use 320px thumbnails", async () => {
  const result = await handleGalleryUpload("trip", ["x.jpg"]);
  assert.deepStrictEqual(result.items, [{ src: "x.jpg", thumb: "x.320w.jpg" }]);
});
''',
        },
        feature={
            "src/mapLimit.js": r'''/**
 * Like Promise.all(items.map(fn)) but with at most `limit` calls to `fn` in flight.
 * Results keep the order of `items`. The first rejection rejects the returned promise and
 * stops new calls from starting; calls already in flight are left to settle.
 */
async function mapLimit(items, limit, fn) {
  if (!Number.isInteger(limit) || limit < 1) {
    throw new RangeError(`limit must be a positive integer, got ${limit}`);
  }
  const results = new Array(items.length);
  let next = 0;
  let failed = false;

  async function worker() {
    while (!failed && next < items.length) {
      const index = next;
      next += 1;
      try {
        results[index] = await fn(items[index], index);
      } catch (err) {
        failed = true;
        throw err;
      }
    }
  }

  const workers = [];
  for (let i = 0; i < Math.min(limit, items.length); i += 1) {
    workers.push(worker());
  }
  await Promise.all(workers);
  return results;
}

module.exports = { mapLimit };
''',
            "src/thumbnails.js": r'''const { resize } = require("./image");
const { mapLimit } = require("./mapLimit");

const DEFAULT_CONCURRENCY = 4;

async function generateThumbnails(paths, size = 256, concurrency = DEFAULT_CONCURRENCY) {
  return mapLimit(paths, concurrency, (path) => resize(path, size));
}

module.exports = { generateThumbnails };
''',
            "test/mapLimit.test.js": r'''const test = require("node:test");
const assert = require("node:assert");
const { mapLimit } = require("../src/mapLimit");

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test("keeps input order when later items finish first", async () => {
  const out = await mapLimit([30, 20, 10, 0], 4, async (ms, index) => {
    await delay(ms);
    return index;
  });
  assert.deepStrictEqual(out, [0, 1, 2, 3]);
});

test("never runs more than limit calls at once", async () => {
  let active = 0;
  let peak = 0;
  await mapLimit(Array.from({ length: 10 }, (_, i) => i), 3, async () => {
    active += 1;
    peak = Math.max(peak, active);
    await delay(5);
    active -= 1;
  });
  assert.strictEqual(peak, 3);
});

test("first failure rejects and stops starting new work", async () => {
  const started = [];
  await assert.rejects(
    mapLimit(["slow", "bad", "c", "d"], 2, async (item) => {
      started.push(item);
      if (item === "bad") {
        throw new Error("boom");
      }
      await delay(item === "slow" ? 30 : 1);
    }),
    /boom/,
  );
  await delay(50);
  assert.deepStrictEqual(started, ["slow", "bad"]);
});

test("empty input resolves to an empty array", async () => {
  assert.deepStrictEqual(await mapLimit([], 4, async () => 1), []);
});

test("rejects a limit that is not a positive integer", async () => {
  await assert.rejects(mapLimit([1], 0, async () => 1), RangeError);
  await assert.rejects(mapLimit([1], 1.5, async () => 1), RangeError);
});
''',
        },
    ),
)
