"""Benchmark cases for code-review skills: small repos with a base commit and a feature branch.

Each case is a realistic pull request. Eight carry one seeded defect of a class that real reviews
miss (several are only visible from code outside the diff, or at runtime); two are clean changes
that a precise reviewer should approve. The answer key lives in ``answers.json``, never in a case.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Case:
    case_id: str
    title: str  # the PR title the author wrote
    body: str  # the PR description the author wrote
    base: dict[str, str]
    feature: dict[str, str]  # files added or replaced on the feature branch
    test_command: str
    removed: tuple[str, ...] = field(default_factory=tuple)


PYTEST_INI = "[pytest]\naddopts = -q\n"

CASES: tuple[Case, ...] = (
    Case(
        case_id="c01-idempotent-charge",
        title="Make POST /charges idempotent",
        body="Clients retry on timeouts and we double-charged twice last month. This stores the "
        "idempotency key with the charge and returns the original charge on a retry.",
        test_command="python -m pytest",
        base={
            "pytest.ini": PYTEST_INI,
            "payments/__init__.py": "",
            "payments/gateway.py": '''"""Thin client for the card processor."""


class Gateway:
    def __init__(self) -> None:
        self.captured: list[tuple[str, int]] = []

    def capture(self, customer_id: str, amount_cents: int) -> str:
        self.captured.append((customer_id, amount_cents))
        return f"txn-{len(self.captured)}"
''',
            "payments/db.py": '''import sqlite3


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5, check_same_thread=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS charges ("
        " charge_id TEXT PRIMARY KEY,"
        " customer_id TEXT NOT NULL,"
        " amount_cents INTEGER NOT NULL,"
        " txn_id TEXT NOT NULL)"
    )
    return conn
''',
            "payments/service.py": '''import sqlite3
import uuid

from payments.gateway import Gateway


def charge(conn: sqlite3.Connection, gateway: Gateway, customer_id: str, amount_cents: int) -> str:
    if amount_cents <= 0:
        raise ValueError("amount must be positive")
    charge_id = uuid.uuid4().hex
    txn_id = gateway.capture(customer_id, amount_cents)
    conn.execute(
        "INSERT INTO charges (charge_id, customer_id, amount_cents, txn_id) VALUES (?, ?, ?, ?)",
        (charge_id, customer_id, amount_cents, txn_id),
    )
    conn.commit()
    return charge_id
''',
            "tests/test_service.py": '''from payments.db import connect
from payments.gateway import Gateway
from payments.service import charge


def test_charge_captures_once(tmp_path):
    conn = connect(str(tmp_path / "p.db"))
    gateway = Gateway()
    charge(conn, gateway, "cus_1", 500)
    assert gateway.captured == [("cus_1", 500)]
''',
        },
        feature={
            "payments/db.py": '''import sqlite3


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5, check_same_thread=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS charges ("
        " charge_id TEXT PRIMARY KEY,"
        " customer_id TEXT NOT NULL,"
        " amount_cents INTEGER NOT NULL,"
        " txn_id TEXT NOT NULL,"
        " idempotency_key TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS charges_idem ON charges (idempotency_key)")
    return conn
''',
            "payments/service.py": '''import sqlite3
import uuid

from payments.gateway import Gateway


def charge(
    conn: sqlite3.Connection,
    gateway: Gateway,
    customer_id: str,
    amount_cents: int,
    idempotency_key: str,
) -> str:
    if amount_cents <= 0:
        raise ValueError("amount must be positive")
    existing = conn.execute(
        "SELECT charge_id FROM charges WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    if existing:
        return existing[0]
    charge_id = uuid.uuid4().hex
    txn_id = gateway.capture(customer_id, amount_cents)
    conn.execute(
        "INSERT INTO charges (charge_id, customer_id, amount_cents, txn_id, idempotency_key)"
        " VALUES (?, ?, ?, ?, ?)",
        (charge_id, customer_id, amount_cents, txn_id, idempotency_key),
    )
    conn.commit()
    return charge_id
''',
            "tests/test_service.py": '''from payments.db import connect
from payments.gateway import Gateway
from payments.service import charge


def test_charge_captures_once(tmp_path):
    conn = connect(str(tmp_path / "p.db"))
    gateway = Gateway()
    charge(conn, gateway, "cus_1", 500, "key-1")
    assert gateway.captured == [("cus_1", 500)]


def test_retry_returns_original_charge(tmp_path):
    conn = connect(str(tmp_path / "p.db"))
    gateway = Gateway()
    first = charge(conn, gateway, "cus_1", 500, "key-1")
    second = charge(conn, gateway, "cus_1", 500, "key-1")
    assert first == second
    assert len(gateway.captured) == 1
''',
        },
    ),
    Case(
        case_id="c02-invoice-pdf",
        title="Add invoice PDF download endpoint",
        body="Customers asked to download invoices as PDF. Adds GET /invoices/:id/pdf next to the "
        "existing invoice routes.",
        test_command="node --test",
        base={
            "package.json": '{\n  "name": "billing",\n  "type": "commonjs",\n  "private": true\n}\n',
            "src/store.js": '''const invoices = new Map([
  ["inv_1", { id: "inv_1", ownerId: "u_1", total: 1200 }],
  ["inv_2", { id: "inv_2", ownerId: "u_2", total: 450 }],
]);

function findInvoice(id) {
  return invoices.get(id) || null;
}

function invoicesFor(ownerId) {
  return [...invoices.values()].filter((inv) => inv.ownerId === ownerId);
}

module.exports = { findInvoice, invoicesFor };
''',
            "src/routes.js": '''const { findInvoice, invoicesFor } = require("./store");

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function loadOwnedInvoice(user, id) {
  const invoice = findInvoice(id);
  if (!invoice || invoice.ownerId !== user.id) {
    throw new HttpError(404, "invoice not found");
  }
  return invoice;
}

const routes = {
  "GET /invoices": (user) => invoicesFor(user.id),
  "GET /invoices/:id": (user, params) => loadOwnedInvoice(user, params.id),
};

module.exports = { routes, HttpError };
''',
            "test/routes.test.js": '''const test = require("node:test");
const assert = require("node:assert");
const { routes, HttpError } = require("../src/routes");

test("owner can read invoice", () => {
  const inv = routes["GET /invoices/:id"]({ id: "u_1" }, { id: "inv_1" });
  assert.strictEqual(inv.total, 1200);
});

test("other users get 404", () => {
  assert.throws(() => routes["GET /invoices/:id"]({ id: "u_1" }, { id: "inv_2" }), HttpError);
});
''',
        },
        feature={
            "src/pdf.js": '''function renderInvoicePdf(invoice) {
  return Buffer.from(`%PDF-1.4\\nInvoice ${invoice.id}\\nTotal ${invoice.total}\\n`);
}

module.exports = { renderInvoicePdf };
''',
            "src/routes.js": '''const { findInvoice, invoicesFor } = require("./store");
const { renderInvoicePdf } = require("./pdf");

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function loadOwnedInvoice(user, id) {
  const invoice = findInvoice(id);
  if (!invoice || invoice.ownerId !== user.id) {
    throw new HttpError(404, "invoice not found");
  }
  return invoice;
}

function downloadInvoicePdf(user, params) {
  const invoice = findInvoice(params.id);
  if (!invoice) {
    throw new HttpError(404, "invoice not found");
  }
  return { contentType: "application/pdf", body: renderInvoicePdf(invoice) };
}

const routes = {
  "GET /invoices": (user) => invoicesFor(user.id),
  "GET /invoices/:id": (user, params) => loadOwnedInvoice(user, params.id),
  "GET /invoices/:id/pdf": downloadInvoicePdf,
};

module.exports = { routes, HttpError };
''',
            "test/pdf.test.js": '''const test = require("node:test");
const assert = require("node:assert");
const { routes } = require("../src/routes");

test("pdf download returns a pdf", () => {
  const res = routes["GET /invoices/:id/pdf"]({ id: "u_1" }, { id: "inv_1" });
  assert.strictEqual(res.contentType, "application/pdf");
  assert.ok(res.body.toString().startsWith("%PDF"));
});
''',
        },
    ),
    Case(
        case_id="c03-paginate",
        title="Paginate the audit log export",
        body="The export endpoint returned 20k rows at once. Adds a paginate() helper and a "
        "page count so the UI can render page links.",
        test_command="python -m pytest",
        base={
            "pytest.ini": PYTEST_INI,
            "audit/__init__.py": "",
            "audit/export.py": '''def export_rows(rows):
    return [dict(row) for row in rows]
''',
            "tests/test_export.py": '''from audit.export import export_rows


def test_export_copies_rows():
    assert export_rows([{"a": 1}]) == [{"a": 1}]
''',
        },
        feature={
            "audit/export.py": '''def export_rows(rows):
    return [dict(row) for row in rows]


def page_count(total_items: int, per_page: int) -> int:
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    return total_items // per_page


def paginate(rows, page: int, per_page: int):
    """Return (items, total_pages) for a 1-based page number."""
    if page < 1:
        raise ValueError("page is 1-based")
    total_pages = page_count(len(rows), per_page)
    start = (page - 1) * per_page
    return export_rows(rows[start : start + per_page]), total_pages
''',
            "tests/test_export.py": '''from audit.export import export_rows, paginate


def test_export_copies_rows():
    assert export_rows([{"a": 1}]) == [{"a": 1}]


def test_paginate_splits_rows():
    rows = [{"n": i} for i in range(40)]
    items, total = paginate(rows, 2, 20)
    assert total == 2
    assert items[0] == {"n": 20}
''',
        },
    ),
    Case(
        case_id="c04-notify-all",
        title="Send renewal reminders to all expiring users",
        body="Adds notifyAll() used by the nightly job. Returns how many reminders were sent so the "
        "job can log it.",
        test_command="node --test",
        base={
            "package.json": '{\n  "name": "reminders",\n  "type": "commonjs",\n  "private": true\n}\n',
            "src/mailer.js": '''const sent = [];

async function sendReminder(user) {
  if (!user.email) {
    throw new Error(`user ${user.id} has no email`);
  }
  await new Promise((resolve) => setTimeout(resolve, 5));
  sent.push(user.email);
}

module.exports = { sendReminder, sent };
''',
            "test/mailer.test.js": '''const test = require("node:test");
const assert = require("node:assert");
const { sendReminder, sent } = require("../src/mailer");

test("sends one reminder", async () => {
  await sendReminder({ id: 1, email: "a@example.com" });
  assert.ok(sent.includes("a@example.com"));
});
''',
        },
        feature={
            "src/jobs.js": '''const { sendReminder } = require("./mailer");

async function notifyAll(users) {
  let count = 0;
  users.forEach(async (user) => {
    await sendReminder(user);
    count += 1;
  });
  return { sent: users.length, attempted: count };
}

module.exports = { notifyAll };
''',
            "test/jobs.test.js": '''const test = require("node:test");
const assert = require("node:assert");
const { notifyAll } = require("../src/jobs");

test("reports reminders sent", async () => {
  const result = await notifyAll([{ id: 1, email: "a@example.com" }, { id: 2, email: "b@example.com" }]);
  assert.strictEqual(result.sent, 2);
});
''',
        },
    ),
    Case(
        case_id="c05-parse-amount",
        title="Fix amount parsing for thousands separators",
        body="Imports from the bank CSV failed on amounts like 1,234.50. Parsing is now tolerant and "
        "the whole suite is green again.",
        test_command="python -m pytest",
        base={
            "pytest.ini": PYTEST_INI,
            "ledger/__init__.py": "",
            "ledger/amounts.py": '''from decimal import Decimal


def parse_amount(text: str) -> int:
    """Parse a decimal amount string into integer cents."""
    value = Decimal(text.strip())
    return int((value * 100).to_integral_value())
''',
            "tests/test_amounts.py": '''from ledger.amounts import parse_amount


def test_plain_amount():
    assert parse_amount("12.50") == 1250


def test_thousands_separator():
    assert parse_amount("1,234.50") == 123450
''',
        },
        feature={
            "ledger/amounts.py": '''from decimal import Decimal


def parse_amount(text: str) -> int:
    """Parse a decimal amount string into integer cents."""
    try:
        value = Decimal(text.strip())
    except Exception:
        return 0
    return int((value * 100).to_integral_value())
''',
            "tests/test_amounts.py": '''from ledger.amounts import parse_amount


def test_plain_amount():
    assert parse_amount("12.50") == 1250


def test_thousands_separator():
    assert parse_amount("1,234.50") is not None
''',
        },
    ),
    Case(
        case_id="c06-customer-search",
        title="Add customer search by name",
        body="Support asked for a name search in the admin panel. Adds search_customers() with a "
        "case-insensitive partial match.",
        test_command="python -m pytest",
        base={
            "pytest.ini": PYTEST_INI,
            "crm/__init__.py": "",
            "crm/repo.py": '''import sqlite3


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    return conn


def get_customer(conn: sqlite3.Connection, customer_id: int):
    return conn.execute("SELECT id, name, email FROM customers WHERE id = ?", (customer_id,)).fetchone()


def add_customer(conn: sqlite3.Connection, name: str, email: str) -> int:
    cur = conn.execute("INSERT INTO customers (name, email) VALUES (?, ?)", (name, email))
    conn.commit()
    return int(cur.lastrowid)
''',
            "tests/test_repo.py": '''from crm.repo import add_customer, connect, get_customer


def test_roundtrip(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    cid = add_customer(conn, "Ada", "ada@example.com")
    assert get_customer(conn, cid)[1] == "Ada"
''',
        },
        feature={
            "crm/repo.py": '''import sqlite3


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    return conn


def get_customer(conn: sqlite3.Connection, customer_id: int):
    return conn.execute("SELECT id, name, email FROM customers WHERE id = ?", (customer_id,)).fetchone()


def add_customer(conn: sqlite3.Connection, name: str, email: str) -> int:
    cur = conn.execute("INSERT INTO customers (name, email) VALUES (?, ?)", (name, email))
    conn.commit()
    return int(cur.lastrowid)


def search_customers(conn: sqlite3.Connection, name: str):
    query = f"SELECT id, name, email FROM customers WHERE lower(name) LIKE '%{name.lower()}%' ORDER BY name"
    return conn.execute(query).fetchall()
''',
            "tests/test_repo.py": '''from crm.repo import add_customer, connect, get_customer, search_customers


def test_roundtrip(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    cid = add_customer(conn, "Ada", "ada@example.com")
    assert get_customer(conn, cid)[1] == "Ada"


def test_search_is_case_insensitive(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    add_customer(conn, "Ada Lovelace", "ada@example.com")
    add_customer(conn, "Alan Turing", "alan@example.com")
    assert [row[1] for row in search_customers(conn, "ADA")] == ["Ada Lovelace"]
''',
        },
    ),
    Case(
        case_id="c07-token-expiry",
        title="Reject expired password-reset tokens",
        body="Reset tokens never expired. Adds is_expired() and checks it in redeem().",
        test_command="python -m pytest",
        base={
            "pytest.ini": PYTEST_INI,
            "auth/__init__.py": "",
            "auth/tokens.py": '''import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TOKEN_TTL = timedelta(hours=1)


@dataclass
class ResetToken:
    value: str
    user_id: int
    expires_at: datetime


def issue(user_id: int) -> ResetToken:
    return ResetToken(secrets.token_urlsafe(16), user_id, datetime.now(timezone.utc) + TOKEN_TTL)


def redeem(token: ResetToken, presented: str) -> int:
    if not secrets.compare_digest(token.value, presented):
        raise PermissionError("invalid token")
    return token.user_id
''',
            "tests/test_tokens.py": '''import pytest

from auth.tokens import issue, redeem


def test_wrong_value_rejected():
    token = issue(7)
    with pytest.raises(PermissionError):
        redeem(token, "nope")
''',
        },
        feature={
            "auth/tokens.py": '''import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TOKEN_TTL = timedelta(hours=1)


@dataclass
class ResetToken:
    value: str
    user_id: int
    expires_at: datetime


def issue(user_id: int) -> ResetToken:
    return ResetToken(secrets.token_urlsafe(16), user_id, datetime.now(timezone.utc) + TOKEN_TTL)


def is_expired(token: ResetToken) -> bool:
    return datetime.utcnow() >= token.expires_at


def redeem(token: ResetToken, presented: str) -> int:
    if not secrets.compare_digest(token.value, presented):
        raise PermissionError("invalid token")
    if is_expired(token):
        raise PermissionError("token expired")
    return token.user_id
''',
            "tests/test_expiry.py": '''from datetime import datetime, timedelta

from auth.tokens import ResetToken, is_expired


def test_old_token_is_expired():
    token = ResetToken("v", 1, datetime.utcnow() - timedelta(minutes=1))
    assert is_expired(token)
''',
        },
    ),
    Case(
        case_id="c08-find-user",
        title="Simplify find_user lookups",
        body="find_user raising for a missing user made every caller wrap it in try/except. It now "
        "returns None, which is more idiomatic. Tests updated.",
        test_command="python -m pytest",
        base={
            "pytest.ini": PYTEST_INI,
            "users/__init__.py": "",
            "users/store.py": '''class UserNotFound(Exception):
    pass


_USERS = {1: {"id": 1, "name": "Ada", "plan": "pro"}}


def find_user(user_id: int) -> dict:
    try:
        return _USERS[user_id]
    except KeyError:
        raise UserNotFound(user_id) from None
''',
            "users/api.py": '''from users.store import UserNotFound, find_user


def get_user_plan(user_id: int) -> tuple[int, dict]:
    try:
        user = find_user(user_id)
    except UserNotFound:
        return 404, {"error": "user not found"}
    return 200, {"plan": user["plan"]}
''',
            "tests/test_store.py": '''import pytest

from users.store import UserNotFound, find_user


def test_find_existing():
    assert find_user(1)["name"] == "Ada"


def test_missing_raises():
    with pytest.raises(UserNotFound):
        find_user(99)
''',
        },
        feature={
            "users/store.py": '''class UserNotFound(Exception):
    pass


_USERS = {1: {"id": 1, "name": "Ada", "plan": "pro"}}


def find_user(user_id: int) -> dict | None:
    return _USERS.get(user_id)
''',
            "tests/test_store.py": '''from users.store import find_user


def test_find_existing():
    assert find_user(1)["name"] == "Ada"


def test_missing_returns_none():
    assert find_user(99) is None
''',
        },
    ),
    Case(
        case_id="c09-clean-refactor",
        title="Extract VAT calculation into a helper",
        body="Pure refactor: the VAT maths was duplicated in two places. No behaviour change.",
        test_command="python -m pytest",
        base={
            "pytest.ini": PYTEST_INI,
            "shop/__init__.py": "",
            "shop/pricing.py": '''from decimal import ROUND_HALF_UP, Decimal

VAT_RATE = Decimal("0.21")


def line_total(unit_price: Decimal, quantity: int) -> Decimal:
    net = unit_price * quantity
    vat = (net * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return net + vat


def shipping_total(fee: Decimal) -> Decimal:
    vat = (fee * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return fee + vat
''',
            "tests/test_pricing.py": '''from decimal import Decimal

from shop.pricing import line_total, shipping_total


def test_line_total():
    assert line_total(Decimal("10.00"), 3) == Decimal("36.30")


def test_shipping_total():
    assert shipping_total(Decimal("4.99")) == Decimal("6.04")
''',
        },
        feature={
            "shop/pricing.py": '''from decimal import ROUND_HALF_UP, Decimal

VAT_RATE = Decimal("0.21")
CENT = Decimal("0.01")


def _with_vat(net: Decimal) -> Decimal:
    vat = (net * VAT_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
    return net + vat


def line_total(unit_price: Decimal, quantity: int) -> Decimal:
    return _with_vat(unit_price * quantity)


def shipping_total(fee: Decimal) -> Decimal:
    return _with_vat(fee)
''',
        },
    ),
    Case(
        case_id="c10-clean-slugify",
        title="Add slugify() for article URLs",
        body="Article URLs used raw titles. Adds slugify() with tests for accents, punctuation and "
        "repeated separators.",
        test_command="node --test",
        base={
            "package.json": '{\n  "name": "blog",\n  "type": "commonjs",\n  "private": true\n}\n',
            "src/articles.js": '''function articleUrl(article) {
  return `/articles/${article.id}`;
}

module.exports = { articleUrl };
''',
            "test/articles.test.js": '''const test = require("node:test");
const assert = require("node:assert");
const { articleUrl } = require("../src/articles");

test("url uses id", () => {
  assert.strictEqual(articleUrl({ id: 3 }), "/articles/3");
});
''',
        },
        feature={
            "src/slugify.js": '''function slugify(title) {
  return String(title)
    .normalize("NFKD")
    .replace(/[\\u0300-\\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

module.exports = { slugify };
''',
            "src/articles.js": '''const { slugify } = require("./slugify");

function articleUrl(article) {
  const slug = slugify(article.title || "");
  return slug ? `/articles/${article.id}-${slug}` : `/articles/${article.id}`;
}

module.exports = { articleUrl };
''',
            "test/articles.test.js": '''const test = require("node:test");
const assert = require("node:assert");
const { articleUrl } = require("../src/articles");
const { slugify } = require("../src/slugify");

test("url uses id and slug", () => {
  assert.strictEqual(articleUrl({ id: 3, title: "Hello, World!" }), "/articles/3-hello-world");
});

test("url without title keeps id only", () => {
  assert.strictEqual(articleUrl({ id: 3 }), "/articles/3");
});

test("slugify strips accents and repeated separators", () => {
  assert.strictEqual(slugify("  Crème  brûlée -- recipe  "), "creme-brulee-recipe");
});
''',
        },
    ),
)
