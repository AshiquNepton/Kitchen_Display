"""SQL Server access for the KDS (pyodbc, raw SQL, same tables as the Java app)."""
import datetime
import json
import os
import re
from contextlib import closing
from pathlib import Path

from django.conf import settings

CONFIG_PATH = Path(settings.DATA_DIR) / "kds_config.json"
REQUIRED = ("server", "database", "username", "password", "driver")
TYPES = {0: "Dine In", 1: "Take Away", 2: "Delivery", 3: "Online"}

_MOJI = re.compile("[ÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßáàâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ]")
_ARABIC = re.compile("[\u0600-\u06FF]")


def fix(text):
    """Same Windows-1256 mojibake repair the Java app does for Arabic names."""
    if not text or _ARABIC.search(text) or not _MOJI.search(text):
        return text
    try:
        return text.encode("latin-1").decode("cp1256")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


# ---------- saved connection settings ----------
def load_config():
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cfg if all(cfg.get(k) for k in REQUIRED) else None


def save_config(cfg):
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


_PREFERRED = ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "ODBC Driver 13 for SQL Server",
              "ODBC Driver 11 for SQL Server", "SQL Server Native Client 11.0", "SQL Server Native Client 10.0",
              "SQL Server"]  # "SQL Server" is the legacy driver built into every Windows install


def drivers():
    """Installed SQL Server ODBC drivers, newest first (the first one is the default on the setup page)."""
    try:
        import pyodbc
        found = [d for d in pyodbc.drivers() if "SQL Server" in d]
    except Exception:
        return []
    return sorted(found, key=lambda d: _PREFERRED.index(d) if d in _PREFERRED else len(_PREFERRED))


# ---------- connection ----------
def _q(v):
    return "{" + str(v).replace("}", "}}") + "}"


def connect(cfg, autocommit=True):
    import pyodbc
    server = cfg["server"]
    if cfg.get("port") and "\\" not in server:
        server += "," + str(cfg["port"])
    cs = (f"DRIVER={{{cfg['driver']}}};SERVER={server};DATABASE={cfg['database']};"
          f"UID={_q(cfg['username'])};PWD={_q(cfg['password'])};")
    if "ODBC Driver" in cfg["driver"]:
        cs += "TrustServerCertificate=yes;Encrypt=no;AutoTranslate=no;"
    conn = pyodbc.connect(cs, timeout=5, autocommit=autocommit)
    conn.setdecoding(pyodbc.SQL_CHAR, encoding="latin-1")  # never raises; fix() repairs Arabic
    return conn


def _rows(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def test_connection(cfg):
    """Return None when OK, otherwise an error message for the setup page."""
    try:
        with closing(connect(cfg)) as conn:
            cur = conn.cursor()
            cur.execute("SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_NAME IN ('TradeMaster','TradeDetails')")
            have = {(r[0], r[1]) for r in cur.fetchall()}
            if not {t for t, _ in have} >= {"TradeMaster", "TradeDetails"}:
                return "Connected, but TradeMaster / TradeDetails were not found. Is this the right database?"
            sql = []
            if ("TradeDetails", "ItemStatus") not in have:
                sql.append("ALTER TABLE TradeDetails ADD ItemStatus INT NOT NULL DEFAULT 0;")
            if sql:
                return "Connected, but the KDS column is missing. Run this on the database, then save again:\n" + "\n".join(sql)
    except Exception as e:
        return str(e)
    return None


# ---------- queries ----------
def fetch_orders(cfg):
    """Today's POS orders (pending and KDS-completed); the browser splits them into tabs."""
    sql = """
        SELECT tm.InvNo, tm.DayRefNo, tm.RefNo, tm.TableNo, tm.ITime,
               DATEDIFF(SECOND, tm.ITime, GETDATE()) AS ElapsedSec,
               w.Description AS WaiterName, c.Description AS CustomerName,
               (SELECT COUNT(*) FROM TradeDetails td
                 WHERE td.TypeCode = 2 AND td.InvNo = tm.InvNo) AS TotalItems,
               (SELECT COUNT(*) FROM TradeDetails td
                 WHERE td.TypeCode = 2 AND td.InvNo = tm.InvNo AND ISNULL(td.ItemStatus, 0) <> 0) AS DoneItems
        FROM TradeMaster tm
        LEFT JOIN ChartOfAccounts w ON tm.RepCode = w.AccountID
        LEFT JOIN ChartOfAccounts c ON tm.AccountID = c.AccountID
        WHERE tm.TypeCode = 2 AND DATEDIFF(DAY, tm.DDate, GETDATE()) = 0
    """ + (" AND tm.CASHCOL <= 0" if cfg.get("unpaid_only", True) else "") + " ORDER BY tm.ITime ASC"
    with closing(connect(cfg)) as conn:
        cur = conn.cursor()
        cur.execute(sql)  # no date parameter: the legacy 'SQL Server' ODBC driver can't bind one
        rows = [r for r in _rows(cur) if r["TotalItems"]]

        def table_id(v):
            try:
                return int(float(str(v)))
            except (TypeError, ValueError):
                return None

        ids = sorted({table_id(r["TableNo"]) for r in rows if r["RefNo"] == 0} - {None})
        tables = {}
        if ids:
            cur.execute("SELECT GroupID, Description FROM ItemGroups WHERE GroupID IN (%s)"
                        % ",".join("?" * len(ids)), ids)
            tables = {int(g): d for g, d in cur.fetchall()}

    out = []
    for r in rows:
        ref = r["RefNo"] if r["RefNo"] in TYPES else 0
        if ref == 0:
            tid = table_id(r["TableNo"])
            name = tables.get(tid) or (f"Table {tid}" if tid else "")
        else:
            name = r["CustomerName"] or ""
        out.append({
            "inv": r["InvNo"], "day_ref": r["DayRefNo"], "type": TYPES[ref], "name": fix(name),
            "waiter": fix(r["WaiterName"] or ""),
            "ordered_at": r["ITime"].isoformat() if r["ITime"] else None,
            "elapsed": max(0, r["ElapsedSec"] or 0),
            "total": r["TotalItems"], "done": r["DoneItems"],
            "kds_done": r["TotalItems"] > 0 and r["DoneItems"] == r["TotalItems"],
        })
    return out


def fetch_items(cfg, inv):
    sql = """
        SELECT td.SlNo, td.Qty, td.Rate, td.Description, ISNULL(td.ItemStatus, 0) AS ItemStatus,
               ii.ItemName, ii.ArabicName
        FROM TradeDetails td LEFT JOIN InventoryItems ii ON td.StockID = ii.ItemID
        WHERE td.TypeCode = 2 AND td.InvNo = ? ORDER BY td.SlNo
    """
    num = lambda v: int(v) if v == int(v) else round(float(v), 2)
    with closing(connect(cfg)) as conn:
        cur = conn.cursor()
        cur.execute(sql, inv)
        return [{"slno": r["SlNo"], "name": fix(r["ItemName"] or ""), "arabic": fix(r["ArabicName"] or ""),
                 "notes": fix(r["Description"] or ""), "qty": num(r["Qty"] or 0),
                 "rate": f"{float(r['Rate'] or 0):.2f}", "done": bool(r["ItemStatus"])}
                for r in _rows(cur)]


def _counts(cur, inv):
    """Recount items from TradeDetails.ItemStatus; an order is KDS-done when every item is done."""
    cur.execute("SELECT COUNT(*), SUM(CASE WHEN ISNULL(ItemStatus,0) <> 0 THEN 1 ELSE 0 END) "
                "FROM TradeDetails WHERE TypeCode = 2 AND InvNo = ?", inv)
    total, done = cur.fetchone()
    total, done = total or 0, done or 0
    return {"total": total, "done": done, "kds_done": bool(total and done == total)}


def _update(cfg, inv, where, params, value):
    with closing(connect(cfg, autocommit=False)) as conn:
        cur = conn.cursor()
        try:
            cur.execute("UPDATE TradeDetails SET ItemStatus = ? WHERE TypeCode = 2 AND InvNo = ?" + where,
                        value, inv, *params)
            if cur.rowcount == 0 and where:
                raise LookupError("Item not found")
            result = _counts(cur, inv)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


def set_item(cfg, inv, slno, done):
    return _update(cfg, inv, " AND SlNo = ?", [slno], 1 if done else 0)


def set_all(cfg, inv):
    return _update(cfg, inv, "", [], 1)
