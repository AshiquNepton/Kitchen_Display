"""SQL Server access for the KDS (pyodbc, raw SQL, same tables as the Java app)."""
import datetime
import json
import os
import re
from contextlib import closing
from pathlib import Path

from django.conf import settings

from kds_project import secure

CONFIG_PATH = Path(settings.DATA_DIR) / "kds.dat"            # encrypted (see kds_project/secure.py)
LEGACY_PATH = Path(settings.DATA_DIR) / "kds_config.json"  # old plain-text file, migrated on first read
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
        cfg = json.loads(secure.unseal(CONFIG_PATH.read_text(encoding="ascii")))
    except (OSError, ValueError):
        try:  # upgrade: an older version stored plain JSON; encrypt it and remove the plain file
            cfg = json.loads(LEGACY_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if all(cfg.get(k) for k in REQUIRED):
            save_config(cfg)
    return cfg if all(cfg.get(k) for k in REQUIRED) else None


def save_config(cfg):
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(secure.seal(json.dumps(cfg)), encoding="ascii")
    os.replace(tmp, CONFIG_PATH)
    try:
        LEGACY_PATH.unlink()
    except OSError:
        pass


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
def fetch_categories(cfg):
    """Return available item categories from ItemGroups where Category = 2 and II.Item_Type = 10."""
    sql = """
        SELECT IG.GroupID, IG.Description, COUNT(II.ItemID) AS ItemCount
        FROM ItemGroups IG
        INNER JOIN InventoryItems II ON IG.GroupID = II.Category
        WHERE II.Item_Type = 10
        AND IG.Category = 2
        GROUP BY IG.GroupID, IG.Description
        ORDER BY IG.Description
    """
    with closing(connect(cfg)) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        cats = [{"id": int(r[0]), "name": fix(r[1] or ""), "count": int(r[2] or 0)} for r in cur.fetchall()]
        if not cats:
            fallback_sql = """
                SELECT IG.GroupID, IG.Description, COUNT(II.ItemID) AS ItemCount
                FROM ItemGroups IG
                INNER JOIN InventoryItems II ON IG.GroupID = II.Category
                WHERE IG.Category = 2
                GROUP BY IG.GroupID, IG.Description
                ORDER BY IG.Description
            """
            cur.execute(fallback_sql)
            cats = [{"id": int(r[0]), "name": fix(r[1] or ""), "count": int(r[2] or 0)} for r in cur.fetchall()]
        return cats


def fetch_orders(cfg, categories=None):
    """Today's POS orders (pending and KDS-completed); filtered by item categories if specified."""
    cat_ids = [int(c) for c in categories if str(c).isdigit()] if categories else []
    if cat_ids:
        cat_list_str = ",".join(str(c) for c in cat_ids)
        cat_total = f"""(SELECT COUNT(*) FROM TradeDetails td
                         INNER JOIN InventoryItems ii ON td.StockID = ii.ItemID
                         WHERE td.TypeCode = 2 AND td.InvNo = tm.InvNo
                         AND ii.Category IN ({cat_list_str}))"""
        cat_done = f"""(SELECT COUNT(*) FROM TradeDetails td
                        INNER JOIN InventoryItems ii ON td.StockID = ii.ItemID
                        WHERE td.TypeCode = 2 AND td.InvNo = tm.InvNo
                        AND ISNULL(td.ItemStatus, 0) <> 0
                        AND ii.Category IN ({cat_list_str}))"""
    else:
        cat_total = """(SELECT COUNT(*) FROM TradeDetails td
                        WHERE td.TypeCode = 2 AND td.InvNo = tm.InvNo)"""
        cat_done = """(SELECT COUNT(*) FROM TradeDetails td
                       WHERE td.TypeCode = 2 AND td.InvNo = tm.InvNo
                       AND ISNULL(td.ItemStatus, 0) <> 0)"""

    sql = f"""
        SELECT tm.InvNo, tm.DayRefNo, tm.RefNo, tm.TableNo, tm.ITime,
               DATEDIFF(SECOND, tm.ITime, GETDATE()) AS ElapsedSec,
               w.Description AS WaiterName, c.Description AS CustomerName,
               {cat_total} AS TotalItems,
               {cat_done} AS DoneItems
        FROM TradeMaster tm
        LEFT JOIN ChartOfAccounts w ON tm.RepCode = w.AccountID
        LEFT JOIN ChartOfAccounts c ON tm.AccountID = c.AccountID
        WHERE tm.TypeCode = 2 AND tm.SaveStatus = -1 AND DATEDIFF(DAY, tm.DDate, GETDATE()) = 0
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

        inv_items = {r["InvNo"]: [] for r in rows}
        if rows:
            inv_nums = [r["InvNo"] for r in rows]
            placeholders = ",".join("?" * len(inv_nums))
            cat_where_items = f" AND ii.Category IN ({','.join(str(c) for c in cat_ids)})" if cat_ids else ""
            items_sql = f"""
                SELECT td.InvNo, td.SlNo, td.Qty, td.Description, td.Notes1, ISNULL(td.ItemStatus, 0) AS ItemStatus,
                       ii.ItemName, ii.ArabicName, ii.Category AS CategoryID, ig.Description AS CategoryName,
                       ig_attr.Description AS AttributeName
                FROM TradeDetails td
                INNER JOIN TradeMaster tm ON td.TypeCode = tm.TypeCode AND td.InvNo = tm.InvNo
                LEFT JOIN InventoryItems ii ON td.StockID = ii.ItemID
                LEFT JOIN ItemGroups ig ON ii.Category = ig.GroupID
                LEFT JOIN ItemGroups ig_attr ON td.WHID = ig_attr.GroupID AND td.WHID > 0
                WHERE td.TypeCode = 2 AND tm.SaveStatus = -1 AND td.InvNo IN ({placeholders})
                {cat_where_items}
                ORDER BY td.InvNo, td.SlNo
            """
            cur.execute(items_sql, inv_nums)
            num = lambda v: int(v) if v == int(v) else round(float(v), 2)
            for it in _rows(cur):
                inv_items[it["InvNo"]].append({
                    "slno": it["SlNo"],
                    "name": fix(it["ItemName"] or it["Description"] or f"Item #{it['SlNo']}").strip(),
                    "arabic": fix(it["ArabicName"] or "").strip(),
                    "notes": fix(it["Description"] or it["Notes1"] or "").strip(),
                    "attribute": fix(it.get("AttributeName") or "").strip(),
                    "qty": num(it["Qty"] or 0),
                    "cat_id": it.get("CategoryID"),
                    "cat_name": fix(it.get("CategoryName") or "").strip(),
                    "done": bool(it["ItemStatus"])
                })

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
            "items": inv_items.get(r["InvNo"], []),
        })
    return out


def fetch_items(cfg, inv, categories=None):
    cat_ids = [int(c) for c in categories if str(c).isdigit()] if categories else []
    cat_where = f" AND ii.Category IN ({','.join(str(c) for c in cat_ids)})" if cat_ids else ""
    sql = f"""
        SELECT td.SlNo, td.Qty, td.Rate, td.Amount, td.Unit, td.Description, td.Notes1, ISNULL(td.ItemStatus, 0) AS ItemStatus,
               ii.ItemName, ii.ArabicName, ii.Category AS CategoryID, ig.Description AS CategoryName,
               ig_attr.Description AS AttributeName
        FROM TradeDetails td
        INNER JOIN TradeMaster tm ON td.TypeCode = tm.TypeCode AND td.InvNo = tm.InvNo
        LEFT JOIN InventoryItems ii ON td.StockID = ii.ItemID
        LEFT JOIN ItemGroups ig ON ii.Category = ig.GroupID
        LEFT JOIN ItemGroups ig_attr ON td.WHID = ig_attr.GroupID AND td.WHID > 0
        WHERE td.TypeCode = 2 AND tm.SaveStatus = -1 AND td.InvNo = ?
        {cat_where}
        ORDER BY td.SlNo
    """
    num = lambda v: int(v) if v == int(v) else round(float(v), 2)
    with closing(connect(cfg)) as conn:
        cur = conn.cursor()
        cur.execute(sql, inv)
        return [{"slno": r["SlNo"], "name": fix(r["ItemName"] or r["Description"] or f"Item #{r['SlNo']}").strip(),
                 "arabic": fix(r["ArabicName"] or "").strip(),
                 "notes": fix(r["Description"] or r["Notes1"] or "").strip(),
                 "attribute": fix(r.get("AttributeName") or "").strip(),
                 "qty": num(r["Qty"] or 0),
                 "rate": f"{float(r['Rate'] or 0):.2f}",
                 "amount": f"{float(r['Amount'] or 0):.2f}",
                 "unit": fix(str(r["Unit"] or "")).strip() if r["Unit"] and str(r["Unit"]) != "0" else "",
                 "cat_id": r.get("CategoryID"),
                 "cat_name": fix(r.get("CategoryName") or "").strip(),
                 "done": bool(r["ItemStatus"])}
                for r in _rows(cur)]


def _counts(cur, inv, categories=None):
    """Recount items from TradeDetails.ItemStatus; an order is KDS-done when every valid item (matching categories if any) is done."""
    cat_ids = [int(c) for c in categories if str(c).isdigit()] if categories else []
    cat_where = f" AND ii.Category IN ({','.join(str(c) for c in cat_ids)})" if cat_ids else ""
    sql = f"""
        SELECT COUNT(*), SUM(CASE WHEN ISNULL(td.ItemStatus, 0) <> 0 THEN 1 ELSE 0 END)
        FROM TradeDetails td
        INNER JOIN TradeMaster tm ON td.TypeCode = tm.TypeCode AND td.InvNo = tm.InvNo
        LEFT JOIN InventoryItems ii ON td.StockID = ii.ItemID
        LEFT JOIN ItemGroups ig ON ii.Category = ig.GroupID
        WHERE td.TypeCode = 2 AND tm.SaveStatus = -1 AND td.InvNo = ?
        {cat_where}
    """
    cur.execute(sql, inv)
    total, done = cur.fetchone()
    total, done = total or 0, done or 0
    return {"total": total, "done": done, "kds_done": bool(total and done == total)}


def _update(cfg, inv, where, params, value, categories=None):
    with closing(connect(cfg, autocommit=False)) as conn:
        cur = conn.cursor()
        try:
            cur.execute("UPDATE TradeDetails SET ItemStatus = ? WHERE TypeCode = 2 AND InvNo = ?" + where,
                        value, inv, *params)
            if cur.rowcount == 0 and where:
                raise LookupError("Item not found")
            result = _counts(cur, inv, categories=categories)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


def set_item(cfg, inv, slno, done, categories=None):
    return _update(cfg, inv, " AND SlNo = ?", [slno], 1 if done else 0, categories=categories)


def set_all(cfg, inv, categories=None):
    cat_ids = [int(c) for c in categories if str(c).isdigit()] if categories else []
    if cat_ids:
        where = f" AND StockID IN (SELECT ItemID FROM InventoryItems WHERE Category IN ({','.join(str(c) for c in cat_ids)}))"
    else:
        where = ""
    return _update(cfg, inv, where, [], 1, categories=categories)