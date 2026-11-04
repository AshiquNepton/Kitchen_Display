import datetime
import hmac
import json
import logging
import os
import socket
import subprocess
import tempfile
import threading
import time
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from . import db

log = logging.getLogger(__name__)

ADMIN_COOKIE, ADMIN_SALT, ADMIN_MINUTES = "kds_admin", "kds.admin", 15


def admin_password():
    """The settings password is today's date as YYMMDD (e.g. 21 Oct 2026 -> 261021)."""
    return datetime.date.today().strftime("%y%m%d")


def is_admin(request):
    return request.get_signed_cookie(ADMIN_COOKIE, default="", salt=ADMIN_SALT, max_age=ADMIN_MINUTES * 60) == "1"


@require_POST
def unlock(request):
    try:
        pw = str(json.loads(request.body or b"{}").get("password", ""))
    except ValueError:
        pw = ""
    if not hmac.compare_digest(pw.encode(), admin_password().encode()):
        return JsonResponse({"error": "Wrong password"}, status=403)
    resp = JsonResponse({"ok": True})
    resp.set_signed_cookie(ADMIN_COOKIE, "1", salt=ADMIN_SALT, max_age=ADMIN_MINUTES * 60, httponly=True, samesite="Lax")
    return resp


@ensure_csrf_cookie
def board(request):
    cfg = db.load_config() or {}
    cats = []
    if cfg:
        try:
            cats = db.fetch_categories(cfg)
        except Exception as e:
            log.warning("Could not pre-fetch categories: %s", e)
    station_cats = cfg.get("station_categories", [])
    return render(request, "kds/board.html", {
        "refresh": settings.KDS_REFRESH_SECONDS,
        "warn": settings.KDS_WARN_MINUTES,
        "late": settings.KDS_LATE_MINUTES,
        "delayed": cfg.get("delayed_after", 0),  # minutes; 0 = off
        "categories": cats,
        "categories_json": json.dumps(cats),
        "station_categories": station_cats,
        "station_categories_json": json.dumps(station_cats),
    })


# Printer/receipt field keys with their defaults
_PRINTER_FIELDS = {
    "printer_type": "windows",
    "printer_name": "",
    "printer_port": "9100",
    "biz_name": "",
    "biz_branch": "",
    "biz_city": "",
    "biz_country": "",
    "biz_phone": "",
    "biz_cr": "",
    "receipt_title": "Receipt",
    "receipt_title_ar": "",
}


def get_installed_printers():
    try:
        import win32print
        return [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
    except Exception:
        return []


def get_default_printer():
    try:
        import win32print
        return win32print.GetDefaultPrinter() or ""
    except Exception:
        return ""


def setup(request):
    saved = db.load_config()
    if saved and not is_admin(request):  # settings are locked once a connection is saved (first run is open)
        return redirect("board")
    drivers = db.drivers()
    installed_printers = get_installed_printers()
    default_printer = get_default_printer()

    form = {"server": "", "port": "1433", "database": "", "username": "",
            "driver": drivers[0] if drivers else "ODBC Driver 18 for SQL Server", "unpaid_only": True,
            "delayed_after": ""}
    form.update(_PRINTER_FIELDS)
    if saved:
        form.update({k: saved.get(k, form[k]) for k in form})
        form["delayed_after"] = form["delayed_after"] or ""  # 0 (off) shows as an empty box
    if not form.get("printer_name") and default_printer:
        form["printer_name"] = default_printer

    error = None
    if request.method == "POST":
        form = {k: request.POST.get(k, "").strip() for k in ("server", "port", "database", "username", "driver", "delayed_after")}
        form["unpaid_only"] = "unpaid_only" in request.POST
        # Collect printer/receipt fields
        for k, default in _PRINTER_FIELDS.items():
            form[k] = request.POST.get(k, default).strip() if isinstance(default, str) else request.POST.get(k, default)

        ptype = request.POST.get("printer_type", "windows")
        form["printer_type"] = ptype
        if ptype == "ip":
            form["printer_name"] = request.POST.get("printer_name_ip", "").strip() or request.POST.get("printer_name", "").strip()
        else:
            form["printer_name"] = request.POST.get("printer_name", "").strip() or default_printer

        cfg = dict(form, password=request.POST.get("password") or (saved or {}).get("password", ""))
        cfg["station_categories"] = _parse_categories(request.POST.getlist("station_categories"))
        if form["delayed_after"] and not form["delayed_after"].isdigit():
            error = "'Show as delayed after' must be a whole number of minutes (or leave it blank)."
        else:
            cfg["delayed_after"] = int(form["delayed_after"] or 0)
            error = db.test_connection(cfg)
        if not error:
            db.save_config(cfg)
            return redirect("board")

    categories = []
    if saved:
        try:
            categories = db.fetch_categories(saved)
        except Exception:
            pass

    return render(request, "kds/setup.html", {
        "form": form,
        "error": error,
        "drivers": drivers,
        "installed_printers": installed_printers,
        "default_printer": default_printer,
        "categories": categories,
        "station_categories": (saved or {}).get("station_categories", []),
        "editing": bool(saved),
    })


@require_POST
def save_station_categories(request):
    """POST /api/station-categories/ — save default category filter for this KDS server."""
    cfg = db.load_config()
    if not cfg:
        return JsonResponse({"error": "Configuration not found."}, status=503)
    try:
        body = json.loads(request.body or b"{}")
        cats = _parse_categories(body.get("categories"))
        cfg["station_categories"] = cats
        db.save_config(cfg)
        return JsonResponse({"ok": True, "categories": cats})
    except Exception as e:
        log.error("Could not save station categories: %s", e)
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


def api(fn):
    @wraps(fn)
    def wrapper(request, *args, **kwargs):
        try:
            return JsonResponse(fn(request, db.load_config(), *args, **kwargs))
        except LookupError as e:
            return JsonResponse({"error": str(e)}, status=404)
        except Exception as e:
            # Server/database details are only shown to the admin; the log keeps just the error type
            log.error("KDS API error (%s)", type(e).__name__, exc_info=settings.DEBUG)
            return JsonResponse({"error": "Database connection failed", "detail": str(e) if is_admin(request) else ""}, status=503)
    return wrapper


def _parse_categories(val):
    """Parse comma-separated or list of category IDs."""
    if not val:
        return []
    if isinstance(val, (list, tuple, set)):
        return [int(x) for x in val if str(x).isdigit()]
    if isinstance(val, str):
        return [int(x.strip()) for x in val.split(",") if x.strip().isdigit()]
    return []


@api
def list_categories(request, cfg):
    """GET /api/categories/ — return all available categories from ItemGroups where Category = 2."""
    return {"categories": db.fetch_categories(cfg)}


@api
def orders(request, cfg):
    cats = _parse_categories(request.GET.get("categories"))
    return {"orders": db.fetch_orders(cfg, categories=cats)}


@api
def items(request, cfg, inv):
    cats = _parse_categories(request.GET.get("categories"))
    return {"items": db.fetch_items(cfg, inv, categories=cats)}


@require_POST
@api
def toggle(request, cfg, inv, slno):
    body = json.loads(request.body or b"{}")
    cats = _parse_categories(body.get("categories"))
    return db.set_item(cfg, inv, slno, bool(body.get("done", True)), categories=cats)


@require_POST
@api
def toggle_body(request, cfg, inv):
    body = json.loads(request.body or b"{}")
    slno = int(body.get("slno", 0))
    cats = _parse_categories(body.get("categories"))
    return db.set_item(cfg, inv, slno, bool(body.get("done", True)), categories=cats)


@require_POST
@api
def complete(request, cfg, inv):
    body = json.loads(request.body or b"{}")
    cats = _parse_categories(body.get("categories"))
    return db.set_all(cfg, inv, categories=cats)


# ---------------------------------------------------------------------------
# Receipt formatting helpers
# ---------------------------------------------------------------------------

def _format_receipt(cfg, order, items_list):
    """Format an 80mm ESC/POS thermal receipt (42-char wide thermal roll)."""
    W = 42
    ESC = b'\x1b'
    GS  = b'\x1d'

    INIT     = ESC + b'\x40'          # initialize printer
    CENTER   = ESC + b'\x61\x01'      # printer hardware center alignment
    LEFT     = ESC + b'\x61\x00'      # printer hardware left alignment
    BOLD_ON  = ESC + b'\x45\x01'      # bold on
    BOLD_OFF = ESC + b'\x45\x00'      # bold off

    DBL_BOTH   = GS + b'\x21\x11'     # double width + double height
    DBL_HEIGHT = GS + b'\x21\x01'     # normal width, double height
    NORMAL     = GS + b'\x21\x00'     # standard size

    CUT = GS + b'\x56\x41\x04'        # feed 4 lines and cut

    biz      = (cfg.get("biz_name") or "").strip()
    branch   = (cfg.get("biz_branch") or "").strip()
    city     = (cfg.get("biz_city") or "").strip()
    country  = (cfg.get("biz_country") or "").strip()
    phone    = (cfg.get("biz_phone") or "").strip()
    cr       = (cfg.get("biz_cr") or "").strip()
    title_en = (cfg.get("receipt_title") or "Receipt").strip()
    title_ar = (cfg.get("receipt_title_ar") or "").strip()

    out = bytearray()
    out += INIT
    out += CENTER

    # 1. Business header
    # If the name is bigger (long), reduce size so it remains centered and does not wrap/clip
    if biz:
        if len(biz) <= 20:
            out += DBL_BOTH + biz.encode("utf-8", errors="replace") + b"\n" + NORMAL
        elif len(biz) <= 32:
            out += DBL_HEIGHT + BOLD_ON + biz.encode("utf-8", errors="replace") + b"\n" + BOLD_OFF + NORMAL
        else:
            out += BOLD_ON + biz[:W].encode("utf-8", errors="replace") + b"\n" + BOLD_OFF

    addr_parts = [p for p in [branch, city, country] if p]
    addr = ", ".join(addr_parts)
    if addr:
        out += addr.encode("utf-8", errors="replace") + b"\n"
    if phone:
        out += f"Tel: {phone}\n".encode("utf-8", errors="replace")
    if cr:
        out += f"CR NO: {cr}\n".encode("utf-8", errors="replace")

    # 2. Receipt heading
    if title_ar:
        out += title_ar.encode("utf-8", errors="replace") + b"\n"
    if title_en:
        out += BOLD_ON + f"* {title_en} *\n".encode("utf-8", errors="replace") + BOLD_OFF

    out += (b"-" * W) + b"\n"

    # 3. Receipt information & 4. Customer information (left aligned)
    out += LEFT

    ordered_at = order.get("ordered_at")
    if ordered_at:
        if isinstance(ordered_at, str):
            dt = datetime.datetime.fromisoformat(ordered_at)
        else:
            dt = ordered_at
        date_str = dt.strftime("%d/%m/%Y")
        time_str = dt.strftime("%H:%M:%S")
    else:
        date_str = time_str = ""

    if date_str:
        out += f"Date: {date_str}  Time: {time_str}\n".encode("utf-8", errors="replace")
    out += f"No.: {order.get('inv', '')}\n".encode("utf-8", errors="replace")

    customer = order.get("name") or order.get("type", "")
    out += f"Customer: {customer}\n".encode("utf-8", errors="replace")

    out += (b"-" * W) + b"\n"

    # 5. Itemized table
    # Columns: [SL NO] | [ITEM_NAME] | [ITEM_QUANTITY] | [UNIT] | [LINE_AMOUNT]
    # Total width: SL(3) Item(20) Qty(4) Unit(5) Total(8) = 40-42
    out += f"{'SL':<3} {'Item':<20} {'Qty':>4} {'Unit':<5} {'Total':>8}\n".encode("utf-8", errors="replace")
    out += (b"-" * W) + b"\n"

    total_qty = 0
    for idx, item in enumerate(items_list, 1):
        name = (item.get("name") or "")[:20]
        qty  = item.get("qty", 0)
        total_qty += qty
        unit = str(item.get("unit") or "")[:5]
        if unit == "0":
            unit = ""
        rate   = float(item.get("rate", 0))
        amount = float(item.get("amount") or (qty * rate))
        out += f"{idx:<3} {name:<20} {qty:>4} {unit:<5} {amount:>8.2f}\n".encode("utf-8", errors="replace")
        attr = (item.get("attribute") or "").strip()
        if attr:
            out += f"    * [{attr}]\n".encode("utf-8", errors="replace")
        notes = (item.get("notes") or "").strip()
        if notes and notes != attr:
            out += f"    * {notes}\n".encode("utf-8", errors="replace")

    # 6. Totals section
    out += (b"-" * W) + b"\n"
    total_str = f"{int(total_qty) if total_qty == int(total_qty) else total_qty}"
    out += BOLD_ON + f"Total: {total_str}\n".encode("utf-8", errors="replace") + BOLD_OFF
    out += (b"=" * W) + b"\n"

    out += b"\n\n\n\n"
    out += CUT
    return bytes(out)


def _send_to_printer(cfg, data):
    """Send raw ESC/POS bytes to the configured printer. Returns None on success, error string on failure."""
    ptype = cfg.get("printer_type", "windows")

    # ------------------------------------------------------------------ IP / TCP
    if ptype == "ip":
        host = (cfg.get("printer_name") or "").strip()
        port = int(cfg.get("printer_port") or 9100)
        if not host:
            return "No printer IP/hostname configured."
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5)
                s.connect((host, port))
                s.sendall(data)
            return None
        except Exception as e:
            return f"IP printer error ({host}:{port}): {e}"

    # ------------------------------------------------------------------ Windows (USB / shared / network)
    printer_name = (cfg.get("printer_name") or "").strip()
    try:
        import win32print
        if not printer_name:
            printer_name = win32print.GetDefaultPrinter()
        if not printer_name:
            return "No Windows printer installed."

        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            win32print.StartDocPrinter(hPrinter, 1, ("KDS Receipt", None, "RAW"))
            try:
                win32print.WritePrinter(hPrinter, data)
            finally:
                win32print.EndDocPrinter(hPrinter)
        finally:
            win32print.ClosePrinter(hPrinter)
        return None

    except ImportError:
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
                f.write(data)
                tmp = f.name
            cmd = f'copy /b "{tmp}" "{printer_name}"' if printer_name else f'copy /b "{tmp}" LPT1'
            result = subprocess.run(cmd, shell=True, timeout=10, capture_output=True)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            if result.returncode != 0:
                return f"Print failed: {result.stderr.decode(errors='replace').strip()}"
            return None
        except Exception as e:
            return str(e)

    except Exception as e:
        return f"Print error ({printer_name}): {e}"


# ---------------------------------------------------------------------------
# Print / printer-list API views
# ---------------------------------------------------------------------------

@require_POST
def print_receipt(request, inv):
    """POST /api/orders/<inv>/print/ — format and send a receipt to the configured printer."""
    cfg = db.load_config()
    if not cfg:
        return JsonResponse({"error": "No database configuration found."}, status=503)
    try:
        body = json.loads(request.body or b"{}")
        cats = _parse_categories(body.get("categories") or request.GET.get("categories"))
        all_orders = db.fetch_orders(cfg, categories=cats)
        order = next((o for o in all_orders if o["inv"] == inv), None)
        if not order:
            fallback = db.fetch_orders(cfg)
            order = next((o for o in fallback if o["inv"] == inv), None)
        if not order:
            return JsonResponse({"error": "Order not found."}, status=404)
        items_list = db.fetch_items(cfg, inv, categories=cats)
        if not items_list:
            return JsonResponse({"ok": False, "error": "No items to print for the selected categories."})
        receipt_data = _format_receipt(cfg, order, items_list)
        err = _send_to_printer(cfg, receipt_data)
        if err:
            return JsonResponse({"ok": False, "error": err})
        return JsonResponse({"ok": True})
    except Exception as e:
        log.error("Print error: %s", e, exc_info=True)
        return JsonResponse({"ok": False, "error": str(e)})


def list_printers(request):
    """GET /api/printers/ — list available Windows printers."""
    printers = []
    try:
        import win32print
        printers = [p[2] for p in win32print.EnumPrinters(
            win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        )]
    except Exception:
        pass
    return JsonResponse({"printers": printers})


@require_POST
def close_app(request):
    """Gracefully terminates KDS application."""
    def _exit():
        time.sleep(0.4)
        os._exit(0)

    threading.Thread(target=_exit, daemon=True).start()
    return JsonResponse({"ok": True})

