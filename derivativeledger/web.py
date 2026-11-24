from decimal import Decimal
import json
from functools import wraps
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from .db import query, execute, mysql_connection
from .security import login_required, roles_required
from .services import execute_trade, record_settlement, flush_audit_outbox, verify_audit_chain, calculate_margin

bp = Blueprint("web", __name__)


def owned_account(account_id):
    if session.get("role") in {"ADMINISTRATOR", "AUDITOR"}:
        return query("SELECT * FROM trader_accounts WHERE id=%s", (account_id,), one=True)
    return query("SELECT * FROM trader_accounts WHERE id=%s AND user_id=%s", (account_id, session["user_id"]), one=True)


@bp.app_context_processor
def inject_user():
    return {"current_user": session.get("user_name"), "current_role": session.get("role")}


@bp.get("/")
def index():
    return redirect(url_for("web.dashboard" if session.get("user_id") else "web.login"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name, email, password = request.form.get("full_name", "").strip(), request.form.get("email", "").strip().lower(), request.form.get("password", "")
        if not name or "@" not in email or len(password) < 10:
            flash("Enter your name, a valid email, and a password of at least 10 characters.", "danger")
        else:
            conn = mysql_connection(); cur = conn.cursor()
            try:
                cur.execute("INSERT INTO users (full_name,email,password_hash,role) VALUES (%s,%s,%s,'TRADER')", (name, email, generate_password_hash(password)))
                uid = cur.lastrowid
                cur.execute("INSERT INTO trader_accounts (user_id,account_name) VALUES (%s,'Primary account')", (uid,))
                account_id = cur.lastrowid
                cur.execute("INSERT INTO margin_accounts (account_id) VALUES (%s)", (account_id,))
                conn.commit()
                session.update(user_id=uid, user_name=name, role="TRADER")
                flash("Account created. Add funds through your administrator before trading.", "success")
                return redirect(url_for("web.dashboard"))
            except Exception as exc:
                conn.rollback()
                flash("Registration failed. That email may already be registered.", "danger")
            finally:
                cur.close()
    return render_template("register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = query("SELECT id, full_name, email, password_hash, role FROM users WHERE email=%s AND is_active=TRUE", (request.form.get("email", "").strip().lower(),), one=True)
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear(); session.update(user_id=user["id"], user_name=user["full_name"], role=user["role"])
            return redirect(url_for("web.dashboard"))
        flash("Email or password was incorrect.", "danger")
    return render_template("login.html")


@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("web.login"))


@bp.get("/dashboard")
@login_required
def dashboard():
    accounts = query("SELECT * FROM trader_accounts WHERE user_id=%s ORDER BY id", (session["user_id"],)) if session["role"] == "TRADER" else query("SELECT * FROM trader_accounts ORDER BY id LIMIT 100")
    account_ids = [a["id"] for a in accounts]
    positions, trades, calls = [], [], []
    if account_ids:
        placeholders = ",".join(["%s"] * len(account_ids))
        positions = query(f"SELECT p.*, c.symbol, c.option_type, c.strike_price, c.expiry_date, c.lot_size, COALESCE((SELECT om.price FROM option_marks om WHERE om.contract_id=c.id ORDER BY om.observed_at DESC,om.id DESC LIMIT 1),c.premium) current_premium, (COALESCE((SELECT om.price FROM option_marks om WHERE om.contract_id=c.id ORDER BY om.observed_at DESC,om.id DESC LIMIT 1),c.premium)-p.average_price)*p.net_quantity*c.lot_size unrealized_pnl FROM positions p JOIN option_contracts c ON c.id=p.contract_id WHERE p.account_id IN ({placeholders}) AND p.status='OPEN' ORDER BY p.updated_at DESC", tuple(account_ids))
        trades = query(f"SELECT t.*, c.symbol FROM trades t JOIN option_contracts c ON c.id=t.contract_id WHERE t.account_id IN ({placeholders}) ORDER BY t.executed_at DESC LIMIT 8", tuple(account_ids))
        calls = query(f"SELECT mc.*, a.account_name FROM margin_calls mc JOIN trader_accounts a ON a.id=mc.account_id WHERE mc.account_id IN ({placeholders}) AND mc.status='OPEN'", tuple(account_ids))
    return render_template("dashboard.html", accounts=accounts, positions=positions, trades=trades, calls=calls)


@bp.get("/instruments")
@login_required
def instruments():
    rows = query("SELECT * FROM instruments ORDER BY symbol")
    contracts = query("SELECT c.*, i.symbol underlying, COALESCE((SELECT om.price FROM option_marks om WHERE om.contract_id=c.id ORDER BY om.observed_at DESC,om.id DESC LIMIT 1),c.premium) current_premium FROM option_contracts c JOIN instruments i ON i.id=c.instrument_id ORDER BY c.expiry_date, c.symbol")
    return render_template("instruments.html", instruments=rows, contracts=contracts)


@bp.post("/admin/instruments")
@roles_required("ADMINISTRATOR")
def add_instrument():
    try:
        execute("INSERT INTO instruments (symbol,name,instrument_type,exchange,currency) VALUES (%s,%s,%s,%s,%s)",
                (request.form["symbol"].strip().upper(), request.form["name"].strip(), request.form["instrument_type"], request.form.get("exchange", "NSE"), request.form.get("currency", "INR").upper()))
        flash("Instrument added.", "success")
    except Exception:
        flash("Could not add instrument. Check that its symbol is unique.", "danger")
    return redirect(url_for("web.instruments"))


@bp.post("/admin/contracts")
@roles_required("ADMINISTRATOR")
def add_contract():
    try:
        execute("INSERT INTO option_contracts (instrument_id,symbol,option_type,strike_price,expiry_date,lot_size,premium) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (int(request.form["instrument_id"]), request.form["symbol"].strip().upper(), request.form["option_type"], request.form["strike_price"], request.form["expiry_date"], int(request.form["lot_size"]), request.form.get("premium", 0)))
        flash("Option contract added.", "success")
    except Exception:
        flash("Could not add contract. Review the fields and unique symbol.", "danger")
    return redirect(url_for("web.instruments"))


@bp.post("/admin/contracts/<int:contract_id>/marks")
@roles_required("ADMINISTRATOR")
def add_option_mark(contract_id):
    try:
        execute("INSERT INTO option_marks (contract_id,price,entered_by) VALUES (%s,%s,%s)", (contract_id, request.form["price"], session["user_id"]))
        flash("Option market mark saved.", "success")
    except Exception:
        flash("Could not save option market mark.", "danger")
    return redirect(url_for("web.instruments"))


@bp.get("/orders")
@login_required
def orders():
    accounts = query("SELECT * FROM trader_accounts WHERE user_id=%s", (session["user_id"],)) if session["role"] == "TRADER" else query("SELECT * FROM trader_accounts")
    clauses, params = [], []
    if session["role"] == "TRADER":
        clauses.append("o.account_id IN (SELECT id FROM trader_accounts WHERE user_id=%s)"); params.append(session["user_id"])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = query("SELECT o.*,a.account_name,c.symbol FROM orders o JOIN trader_accounts a ON a.id=o.account_id JOIN option_contracts c ON c.id=o.contract_id" + where + " ORDER BY o.created_at DESC LIMIT 200", tuple(params))
    contracts = query("SELECT id,symbol,option_type,strike_price,expiry_date FROM option_contracts WHERE status='ACTIVE' AND expiry_date>=CURDATE() ORDER BY expiry_date,symbol")
    return render_template("orders.html", orders=rows, accounts=accounts, contracts=contracts)


@bp.post("/orders/<int:order_id>/cancel")
@login_required
def cancel_order(order_id):
    conditions, params = ["id=%s", "status='OPEN'"], [order_id]
    if session["role"] == "TRADER":
        conditions.append("account_id IN (SELECT id FROM trader_accounts WHERE user_id=%s)"); params.append(session["user_id"])
    _, count = execute("UPDATE orders SET status='CANCELLED' WHERE " + " AND ".join(conditions), tuple(params))
    flash("Order cancelled." if count else "No open order found to cancel.", "success" if count else "warning")
    return redirect(url_for("web.orders"))


@bp.post("/orders")
@login_required
def place_order():
    account_id, contract_id, side = int(request.form["account_id"]), int(request.form["contract_id"]), request.form["side"]
    account = owned_account(account_id)
    if not account:
        flash("Trading account not found.", "danger"); return redirect(url_for("web.orders"))
    order_id = None
    try:
        qty, price, fees = int(request.form["quantity"]), Decimal(request.form["price"]), Decimal(request.form.get("fees", "0"))
        conn = mysql_connection(); cur = conn.cursor()
        cur.execute("INSERT INTO orders (account_id,contract_id,side,quantity,order_type,status) VALUES (%s,%s,%s,%s,'MARKET','OPEN')", (account_id, contract_id, side, qty))
        order_id = cur.lastrowid; conn.commit(); cur.close()
        result = execute_trade(account_id, contract_id, side, qty, price, fees, order_id)
        execute("UPDATE orders SET status='EXECUTED' WHERE id=%s", (order_id,))
        flash(f"Trade executed (#{result['trade_id']}); position quantity is {result['net_quantity']}.", "success")
    except Exception as exc:
        if order_id:
            try: execute("UPDATE orders SET status='REJECTED' WHERE id=%s AND status='OPEN'", (order_id,))
            except Exception: pass
        flash(f"Order was not executed: {exc}", "danger")
    return redirect(url_for("web.orders"))


@bp.get("/portfolio")
@login_required
def portfolio():
    accounts = query("SELECT * FROM trader_accounts WHERE user_id=%s", (session["user_id"],)) if session["role"] == "TRADER" else query("SELECT * FROM trader_accounts")
    positions = query("SELECT p.*,a.account_name,c.symbol,c.option_type,c.strike_price,c.expiry_date,c.lot_size,COALESCE((SELECT om.price FROM option_marks om WHERE om.contract_id=c.id ORDER BY om.observed_at DESC,om.id DESC LIMIT 1),c.premium) current_premium,(COALESCE((SELECT om.price FROM option_marks om WHERE om.contract_id=c.id ORDER BY om.observed_at DESC,om.id DESC LIMIT 1),c.premium)-p.average_price)*p.net_quantity*c.lot_size unrealized_pnl FROM positions p JOIN trader_accounts a ON a.id=p.account_id JOIN option_contracts c ON c.id=p.contract_id WHERE p.status='OPEN'" + (" AND a.user_id=%s" if session["role"] == "TRADER" else "") + " ORDER BY p.updated_at DESC", (session["user_id"],) if session["role"] == "TRADER" else ())
    return render_template("portfolio.html", positions=positions, accounts=accounts)


@bp.get("/portfolio/reconstruct")
@login_required
def reconstruct_portfolio():
    accounts = query("SELECT * FROM trader_accounts WHERE user_id=%s" if session.get("role") == "TRADER" else "SELECT * FROM trader_accounts", (session["user_id"],) if session.get("role") == "TRADER" else ())
    as_of = request.args.get("as_of", "")
    if not as_of:
        from datetime import date
        as_of = date.today().isoformat()
    try:
        from datetime import date as date_type
        cutoff = date_type.fromisoformat(as_of)
    except ValueError:
        flash("Choose a valid reconstruction date.", "danger")
        return redirect(url_for("web.portfolio"))
    params = [cutoff]
    sql = "SELECT t.id,t.account_id,a.account_name,t.contract_id,c.symbol,c.instrument_id,c.option_type,c.strike_price,c.expiry_date,c.lot_size,t.side,t.quantity,t.price,t.fees,t.executed_at FROM trades t JOIN trader_accounts a ON a.id=t.account_id JOIN option_contracts c ON c.id=t.contract_id WHERE DATE(t.executed_at)<=%s"
    if session["role"] == "TRADER":
        sql += " AND a.user_id=%s"; params.append(session["user_id"])
    sql += " ORDER BY t.account_id,t.contract_id,t.executed_at,t.id"
    trades = query(sql, tuple(params))
    adjustment_rows = query("SELECT ca.effective_date,ca.ratio_num,ca.ratio_den,ca.action_type,caa.contract_id FROM corporate_actions ca JOIN contract_adjustments caa ON caa.corporate_action_id=ca.id ORDER BY ca.effective_date,ca.id")
    # Contract terms are adjusted forward by split/bonus factors. Translate historic
    # per-share trade prices into the selected date's units before replaying FIFO.
    adjustments = {}
    for action in adjustment_rows:
        adjustments.setdefault(action["contract_id"], []).append((action["effective_date"], Decimal(action["ratio_num"])/Decimal(action["ratio_den"]), action["action_type"]))
    books = {}
    for trade in trades:
        key = (trade["account_id"], trade["contract_id"])
        action_list = adjustments.get(trade["contract_id"], [])
        actions_until = [item for item in action_list if item[2] in {"SPLIT", "REVERSE_SPLIT", "BONUS"}]
        lot_asof = Decimal(int(trade["lot_size"]))
        strike_asof = Decimal(trade["strike_price"])
        for effective, factor, _kind in actions_until:
            if effective > cutoff:
                lot_asof /= factor
                strike_asof *= factor
        state = books.setdefault(key, {"account_id":trade["account_id"],"account_name": trade["account_name"], "contract_id":trade["contract_id"], "symbol": trade["symbol"], "instrument_id":trade["instrument_id"],"option_type": trade["option_type"], "strike_price": strike_asof, "expiry_date": trade["expiry_date"], "lot_size": int(lot_asof), "quantity": 0, "lots": [], "realized_pnl": Decimal("0"), "first_trade": trade["executed_at"], "last_trade": trade["executed_at"]})
        trade_date = trade["executed_at"].date() if hasattr(trade["executed_at"], "date") else trade["executed_at"]
        scale = Decimal(1)
        for effective, factor, kind in actions_until:
            if trade_date < effective <= cutoff: scale *= factor
        qty, price, fees = int(trade["quantity"]), Decimal(trade["price"])/scale, Decimal(trade["fees"])
        signed = qty if trade["side"] == "BUY" else -qty
        unit_cost = price + fees / Decimal(qty * state["lot_size"]) * (1 if signed > 0 else -1)
        remaining = qty
        if state["quantity"] and (state["quantity"] > 0) != (signed > 0):
            for lot in state["lots"]:
                if remaining == 0: break
                used = min(remaining, lot["quantity"])
                state["realized_pnl"] += (price-lot["cost"])*used*state["lot_size"]*(1 if state["quantity"] > 0 else -1) - fees*Decimal(used)/Decimal(qty)
                lot["quantity"] -= used
                remaining -= used
        new_quantity = state["quantity"] + signed
        if remaining:
            state["lots"].append({"quantity": remaining, "cost": unit_cost, "direction": "LONG" if signed > 0 else "SHORT"})
        state["lots"] = [lot for lot in state["lots"] if lot["quantity"]]
        state["quantity"] = new_quantity
        state["last_trade"] = trade["executed_at"]
    positions = []
    for account in accounts:
        cash_row = query("SELECT COALESCE(SUM(amount),0) balance FROM financial_transactions WHERE account_id=%s AND DATE(created_at)<=%s", (account["id"],cutoff), one=True)
        account["historical_cash"] = cash_row["balance"]
    for state in books.values():
        if state["quantity"]:
            remaining_qty = sum(lot["quantity"] for lot in state["lots"])
            average = sum(lot["cost"]*lot["quantity"] for lot in state["lots"])/remaining_qty if remaining_qty else Decimal(0)
            net_qty = state.pop("quantity")
            option_mark = query("SELECT price FROM option_marks WHERE contract_id=%s AND observed_at<=%s ORDER BY observed_at DESC,id DESC LIMIT 1", (state["contract_id"],cutoff), one=True)
            spot_mark = query("SELECT price FROM market_marks WHERE instrument_id=%s AND observed_at<=%s ORDER BY observed_at DESC,id DESC LIMIT 1", (state["instrument_id"],cutoff), one=True)
            mark = Decimal(option_mark["price"]) if option_mark else None
            spot = Decimal(spot_mark["price"]) if spot_mark else None
            unrealized = (mark-average)*net_qty*state["lot_size"] if mark is not None else None
            margin_estimate = Decimal(0)
            if net_qty < 0 and spot is not None:
                otm = max(Decimal(0),state["strike_price"]-spot) if state["option_type"]=="CALL" else max(Decimal(0),spot-state["strike_price"])
                margin_estimate = ((mark or Decimal(0)) + max(spot*Decimal("0.20")-otm,spot*Decimal("0.10"))) * abs(net_qty)*state["lot_size"]
            state.update(net_quantity=net_qty, average_price=average, realized_pnl=state["realized_pnl"].quantize(Decimal("0.01")), option_mark=mark, underlying_mark=spot, unrealized_pnl=unrealized, margin_requirement=margin_estimate)
            positions.append(state)
    return render_template("reconstruct.html", positions=positions, as_of=as_of)


@bp.post("/positions/<int:position_id>/settle")
@roles_required("TRADER", "ADMINISTRATOR")
def settle(position_id):
    position = query("SELECT p.id,p.account_id,a.user_id FROM positions p JOIN trader_accounts a ON a.id=p.account_id WHERE p.id=%s", (position_id,), one=True)
    if not position or (session["role"] == "TRADER" and position["user_id"] != session["user_id"]):
        flash("Position not found.", "danger"); return redirect(url_for("web.portfolio"))
    try:
        sid = record_settlement(position_id, session["user_id"], request.form["settlement_price"], request.form.get("notes", ""))
        flash(f"Position settled (#{sid}). Audit recording is complete if MongoDB was available.", "success")
    except Exception as exc:
        flash(f"Settlement failed: {exc}", "danger")
    return redirect(url_for("web.portfolio"))


@bp.get("/history")
@login_required
def history():
    account_id = request.args.get("account_id", type=int)
    accounts = query("SELECT * FROM trader_accounts WHERE user_id=%s", (session["user_id"],)) if session["role"] == "TRADER" else query("SELECT * FROM trader_accounts")
    allowed_ids = {a["id"] for a in accounts}
    account_id = account_id if account_id in allowed_ids else (accounts[0]["id"] if accounts else None)
    trades = query("SELECT t.*,c.symbol FROM trades t JOIN option_contracts c ON c.id=t.contract_id WHERE t.account_id=%s ORDER BY t.executed_at DESC", (account_id,)) if account_id else []
    settlements = query("SELECT s.*,p.contract_id,c.symbol FROM settlement_events s JOIN positions p ON p.id=s.position_id JOIN option_contracts c ON c.id=p.contract_id WHERE s.account_id=%s ORDER BY s.settled_at DESC", (account_id,)) if account_id else []
    transactions = query("SELECT * FROM financial_transactions WHERE account_id=%s ORDER BY created_at DESC", (account_id,)) if account_id else []
    return render_template("history.html", accounts=accounts, account_id=account_id, trades=trades, settlements=settlements, transactions=transactions)


@bp.get("/reports")
@login_required
def reports():
    params, filter_sql = [], ""
    if session["role"] == "TRADER":
        filter_sql = " WHERE a.user_id=%s"; params.append(session["user_id"])
    summary = query("SELECT a.account_name,a.currency,a.cash_balance,COALESCE(SUM(p.realized_pnl),0) realized_pnl,COALESCE(SUM(CASE WHEN p.status='OPEN' THEN (COALESCE((SELECT om.price FROM option_marks om WHERE om.contract_id=c.id ORDER BY om.observed_at DESC,om.id DESC LIMIT 1),c.premium)-p.average_price)*p.net_quantity*c.lot_size ELSE 0 END),0) unrealized_pnl,COUNT(DISTINCT CASE WHEN p.status='OPEN' THEN p.id END) open_positions FROM trader_accounts a LEFT JOIN positions p ON p.account_id=a.id LEFT JOIN option_contracts c ON c.id=p.contract_id" + filter_sql + " GROUP BY a.id ORDER BY a.account_name", tuple(params))
    return render_template("reports.html", summary=summary)


@bp.get("/margin")
@login_required
def margin():
    accounts = query("SELECT a.*,m.available_margin,m.required_margin FROM trader_accounts a LEFT JOIN margin_accounts m ON m.account_id=a.id" + (" WHERE a.user_id=%s" if session["role"] == "TRADER" else "") + " ORDER BY a.id", (session["user_id"],) if session["role"] == "TRADER" else ())
    marks = query("SELECT i.symbol,m.price,m.observed_at FROM instruments i JOIN market_marks m ON m.id=(SELECT m2.id FROM market_marks m2 WHERE m2.instrument_id=i.id ORDER BY m2.observed_at DESC,m2.id DESC LIMIT 1) ORDER BY i.symbol")
    calls = query("SELECT mc.*,a.account_name FROM margin_calls mc JOIN trader_accounts a ON a.id=mc.account_id WHERE mc.status='OPEN'" + (" AND a.user_id=%s" if session["role"] == "TRADER" else ""), (session["user_id"],) if session["role"] == "TRADER" else ())
    instruments = query("SELECT id,symbol FROM instruments WHERE status='ACTIVE' ORDER BY symbol")
    return render_template("margin.html", accounts=accounts, marks=marks, calls=calls, instruments=instruments)


@bp.post("/margin/<int:account_id>/recalculate")
@roles_required("TRADER", "ADMINISTRATOR")
def recalculate_margin(account_id):
    if not owned_account(account_id):
        flash("Trading account not found.", "danger")
    else:
        try:
            result = calculate_margin(account_id)
            flash(f"Margin requirement: {result['required']}; available cash: {result['available']}.", "warning" if result["call"] else "success")
        except Exception as exc:
            flash(f"Margin was not recalculated: {exc}", "danger")
    return redirect(url_for("web.margin"))


@bp.post("/admin/marks")
@roles_required("ADMINISTRATOR")
def add_market_mark():
    try:
        execute("INSERT INTO market_marks (instrument_id,price,entered_by) VALUES (%s,%s,%s)", (int(request.form["instrument_id"]), request.form["price"], session["user_id"]))
        flash("Underlying market mark saved.", "success")
    except Exception:
        flash("Could not save market mark.", "danger")
    return redirect(url_for("web.margin"))


@bp.route("/scenarios", methods=["GET", "POST"])
@login_required
def scenarios():
    accounts = query("SELECT * FROM trader_accounts WHERE user_id=%s", (session["user_id"],)) if session["role"] == "TRADER" else query("SELECT * FROM trader_accounts")
    if request.method == "POST":
        if session["role"] == "AUDITOR":
            flash("Auditors have read-only access.", "danger")
            return redirect(url_for("web.scenarios"))
        account = owned_account(int(request.form["account_id"]))
        if not account:
            flash("Trading account not found.", "danger")
        else:
            execute("INSERT INTO scenarios (account_id,name,description) VALUES (%s,%s,%s)", (account["id"], request.form["name"].strip(), request.form.get("description", "").strip()))
            flash("Scenario created. Add hypothetical trades from its detail page.", "success")
            return redirect(url_for("web.scenarios"))
    ids = [a["id"] for a in accounts]
    rows = []
    if ids:
        ph = ",".join(["%s"]*len(ids))
        rows = query(f"SELECT s.*,a.account_name FROM scenarios s JOIN trader_accounts a ON a.id=s.account_id WHERE s.account_id IN ({ph}) ORDER BY s.created_at DESC", tuple(ids))
    contracts = query("SELECT id,symbol FROM option_contracts WHERE status='ACTIVE'")
    details = {}
    metrics = {}
    for scenario in rows:
        details[scenario["id"]] = query("SELECT st.*,c.symbol,c.lot_size FROM scenario_trades st JOIN option_contracts c ON c.id=st.contract_id WHERE st.scenario_id=%s ORDER BY st.created_at", (scenario["id"],))
        live = query("SELECT p.contract_id,p.net_quantity,p.average_price,p.realized_pnl,c.symbol,c.lot_size,c.strike_price,c.option_type,COALESCE((SELECT om.price FROM option_marks om WHERE om.contract_id=c.id ORDER BY om.observed_at DESC,om.id DESC LIMIT 1),c.premium) mark,(SELECT mm.price FROM market_marks mm JOIN instruments ii ON ii.id=mm.instrument_id WHERE ii.id=c.instrument_id ORDER BY mm.observed_at DESC,mm.id DESC LIMIT 1) spot FROM positions p JOIN option_contracts c ON c.id=p.contract_id WHERE p.account_id=%s AND p.status='OPEN'", (scenario["account_id"],))
        book = {p["contract_id"]: {"symbol": p["symbol"], "quantity": int(p["net_quantity"]), "average": Decimal(p["average_price"]), "lot_size": int(p["lot_size"]), "mark": Decimal(p["mark"]), "realized": Decimal(p["realized_pnl"]), "strike": Decimal(p["strike_price"]), "option_type": p["option_type"], "spot": Decimal(p["spot"]) if p["spot"] is not None else None} for p in live}
        live_pnl = sum((Decimal(p["realized_pnl"])+(Decimal(p["mark"])-Decimal(p["average_price"])) * int(p["net_quantity"]) * int(p["lot_size"])) for p in live)
        for trade in details[scenario["id"]]:
            contract = query("SELECT c.lot_size,c.strike_price,c.option_type,COALESCE((SELECT om.price FROM option_marks om WHERE om.contract_id=c.id ORDER BY om.observed_at DESC,om.id DESC LIMIT 1),c.premium) mark,(SELECT mm.price FROM market_marks mm WHERE mm.instrument_id=c.instrument_id ORDER BY mm.observed_at DESC,mm.id DESC LIMIT 1) spot FROM option_contracts c WHERE c.id=%s", (trade["contract_id"],), one=True)
            item = book.setdefault(trade["contract_id"], {"symbol": trade["symbol"], "quantity": 0, "average": Decimal(0), "lot_size": int(contract["lot_size"]), "mark": Decimal(contract["mark"]), "realized": Decimal(0), "strike": Decimal(contract["strike_price"]), "option_type": contract["option_type"], "spot": Decimal(contract["spot"]) if contract["spot"] is not None else None})
            qty, price = int(trade["quantity"]), Decimal(trade["price"])
            signed = qty if trade["side"] == "BUY" else -qty
            old_qty = item["quantity"]
            if old_qty == 0 or (old_qty > 0) == (signed > 0):
                item["average"] = (abs(old_qty)*item["average"] + qty*price) / (abs(old_qty)+qty)
            else:
                closed = min(abs(old_qty), qty)
                item["realized"] += (price-item["average"])*closed*item["lot_size"]*(1 if old_qty > 0 else -1)
                if qty > abs(old_qty): item["average"] = price
                elif qty == abs(old_qty): item["average"] = Decimal(0)
            item["quantity"] += signed
        projected_pnl = sum((item["mark"]-item["average"]) * item["quantity"] * item["lot_size"] + item["realized"] for item in book.values())
        live_exposure = sum(abs(int(p["net_quantity"])) * int(p["lot_size"]) * Decimal(p["mark"]) for p in live)
        projected_exposure = sum(abs(item["quantity"]) * item["lot_size"] * item["mark"] for item in book.values())
        def estimated_margin(items):
            total = Decimal(0)
            for item in items:
                if item["quantity"] >= 0 or item["spot"] is None: continue
                spot, strike = item["spot"], item["strike"]
                otm = max(Decimal(0), strike-spot) if item["option_type"] == "CALL" else max(Decimal(0), spot-strike)
                total += (item["mark"] + max(spot*Decimal("0.20")-otm, spot*Decimal("0.10"))) * abs(item["quantity"]) * item["lot_size"]
            return total
        live_margin = estimated_margin([{"quantity":int(p["net_quantity"]),"mark":Decimal(p["mark"]),"spot":Decimal(p["spot"]) if p["spot"] is not None else None,"strike":Decimal(p["strike_price"]),"option_type":p["option_type"],"lot_size":int(p["lot_size"])} for p in live])
        projected_margin = estimated_margin(list(book.values()))
        metrics[scenario["id"]] = {"live_pnl": live_pnl, "projected_pnl": projected_pnl, "pnl_change": projected_pnl-live_pnl,
                                    "live_exposure": live_exposure, "projected_exposure": projected_exposure, "live_margin": live_margin, "projected_margin": projected_margin,
                                    "positions": [item for item in book.values() if item["quantity"]]}
    return render_template("scenarios.html", accounts=accounts, scenarios=rows, contracts=contracts, details=details, metrics=metrics)


@bp.post("/scenarios/<int:scenario_id>/trades")
@roles_required("TRADER", "ADMINISTRATOR")
def scenario_trade(scenario_id):
    scenario = query("SELECT s.*,a.user_id FROM scenarios s JOIN trader_accounts a ON a.id=s.account_id WHERE s.id=%s", (scenario_id,), one=True)
    if not scenario or (session["role"] == "TRADER" and scenario["user_id"] != session["user_id"]):
        flash("Scenario not found.", "danger"); return redirect(url_for("web.scenarios"))
    execute("INSERT INTO scenario_trades (scenario_id,contract_id,side,quantity,price) VALUES (%s,%s,%s,%s,%s)", (scenario_id, int(request.form["contract_id"]), request.form["side"], int(request.form["quantity"]), request.form["price"]))
    flash("Hypothetical trade saved; live account data was not changed.", "success")
    return redirect(url_for("web.scenarios"))


@bp.get("/audit")
@roles_required("ADMINISTRATOR", "AUDITOR")
def audit():
    status = request.args.get("status")
    flush_error = None
    if status == "flush" and session["role"] == "ADMINISTRATOR":
        try:
            flush_audit_outbox()
            flash("Pending audit records were processed.", "success")
        except Exception as exc:
            flush_error = str(exc)
    rows = query("SELECT s.*,u.full_name,a.account_name FROM settlement_events s JOIN users u ON u.id=s.settled_by JOIN trader_accounts a ON a.id=s.account_id ORDER BY s.settled_at DESC LIMIT 200")
    try:
        verification = verify_audit_chain()
    except Exception as exc:
        verification = {"valid": False, "checked": 0, "error": str(exc)}
    return render_template("audit.html", settlements=rows, verification=verification, flush_error=flush_error)


@bp.get("/admin/users")
@roles_required("ADMINISTRATOR")
def users():
    rows = query("SELECT id,full_name,email,role,is_active,created_at FROM users ORDER BY created_at DESC")
    return render_template("users.html", users=rows)


@bp.post("/admin/users/<int:user_id>/role")
@roles_required("ADMINISTRATOR")
def update_role(user_id):
    role = request.form.get("role")
    if role not in {"TRADER", "ADMINISTRATOR", "AUDITOR"} or user_id == session["user_id"]:
        flash("Invalid role update.", "danger")
    else:
        execute("UPDATE users SET role=%s WHERE id=%s", (role, user_id)); flash("Role updated.", "success")
    return redirect(url_for("web.users"))


@bp.post("/admin/accounts/<int:account_id>/deposit")
@roles_required("ADMINISTRATOR")
def deposit(account_id):
    amount = Decimal(request.form.get("amount", "0"))
    if amount <= 0:
        flash("Deposit must be greater than zero.", "danger")
    else:
        conn = mysql_connection(); cur = conn.cursor()
        try:
            cur.execute("UPDATE trader_accounts SET cash_balance=cash_balance+%s WHERE id=%s", (amount, account_id))
            if not cur.rowcount: raise ValueError("Account not found")
            cur.execute("INSERT INTO financial_transactions (account_id,transaction_type,amount,description) VALUES (%s,'DEPOSIT',%s,'Administrator funding')", (account_id, amount))
            conn.commit(); flash("Funds deposited.", "success")
        except Exception:
            conn.rollback(); flash("Deposit failed.", "danger")
        finally: cur.close()
    return redirect(url_for("web.dashboard"))


@bp.get("/admin/corporate-actions")
@roles_required("ADMINISTRATOR", "AUDITOR")
def corporate_actions():
    actions = query("SELECT ca.*,i.symbol,u.full_name FROM corporate_actions ca JOIN instruments i ON i.id=ca.instrument_id JOIN users u ON u.id=ca.created_by ORDER BY ca.effective_date DESC")
    instruments = query("SELECT id,symbol FROM instruments ORDER BY symbol")
    return render_template("corporate_actions.html", actions=actions, instruments=instruments)


@bp.post("/admin/corporate-actions")
@roles_required("ADMINISTRATOR")
def create_corporate_action():
    try:
        conn = mysql_connection(); cur = conn.cursor(dictionary=True)
        action_type = request.form["action_type"]
        instrument_id = int(request.form["instrument_id"])
        num, den = Decimal(request.form.get("ratio_num",1)), Decimal(request.form.get("ratio_den",1))
        if num <= 0 or den <= 0:
            raise ValueError("Corporate action ratio must be positive.")
        cur.execute("INSERT INTO corporate_actions (instrument_id,action_type,ratio_num,ratio_den,cash_per_share,effective_date,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (instrument_id, action_type, num, den, request.form.get("cash_per_share",0), request.form["effective_date"], session["user_id"]))
        action_id = cur.lastrowid
        adjusted = 0
        # A split/bonus ratio is expressed as new shares divided by old shares.
        # Special dividends are recorded for audit; exchange-specific option adjustments need an explicit rule.
        if action_type in {"SPLIT", "REVERSE_SPLIT", "BONUS"}:
            factor = num / den
            cur.execute("SELECT id,strike_price,lot_size,premium FROM option_contracts WHERE instrument_id=%s AND status='ACTIVE' AND expiry_date>=%s FOR UPDATE", (instrument_id, request.form["effective_date"]))
            contracts = cur.fetchall()
            for c in contracts:
                old_strike, old_lot = Decimal(c["strike_price"]), int(c["lot_size"])
                exact_lot = Decimal(old_lot) * factor
                if exact_lot != exact_lot.to_integral_value():
                    raise ValueError(f"Action ratio creates a fractional lot size for contract #{c['id']}.")
                new_lot, new_strike = int(exact_lot), old_strike/factor
                cur.execute("INSERT INTO contract_adjustments (corporate_action_id,contract_id,original_strike,adjusted_strike,original_lot_size,adjusted_lot_size) VALUES (%s,%s,%s,%s,%s,%s)", (action_id,c["id"],old_strike,new_strike,old_lot,new_lot))
                cur.execute("UPDATE option_contracts SET strike_price=%s,lot_size=%s,premium=premium/%s WHERE id=%s", (new_strike,new_lot,factor,c["id"]))
                cur.execute("SELECT price FROM option_marks WHERE contract_id=%s ORDER BY observed_at DESC,id DESC LIMIT 1 FOR UPDATE", (c["id"],))
                option_mark = cur.fetchone()
                if option_mark:
                    cur.execute("INSERT INTO option_marks (contract_id,price,entered_by) VALUES (%s,%s,%s)", (c["id"],Decimal(option_mark["price"])/factor,session["user_id"]))
                cur.execute("SELECT id,net_quantity,average_price FROM positions WHERE contract_id=%s AND status='OPEN' FOR UPDATE", (c["id"],))
                for position in cur.fetchall():
                    cur.execute("SELECT id,remaining_quantity,unit_cost FROM position_lots WHERE position_id=%s ORDER BY id FOR UPDATE", (position["id"],))
                    lots = cur.fetchall()
                    before = [{"id":lot["id"],"remaining_quantity":lot["remaining_quantity"],"unit_cost":str(lot["unit_cost"])} for lot in lots]
                    after = [{"id":lot["id"],"remaining_quantity":lot["remaining_quantity"],"unit_cost":str(Decimal(lot["unit_cost"])/factor)} for lot in lots]
                    cur.execute("INSERT INTO position_adjustments (corporate_action_id,position_id,quantity_before,quantity_after,average_price_before,average_price_after,lots_before,lots_after) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                                (action_id,position["id"],position["net_quantity"],position["net_quantity"],position["average_price"],Decimal(position["average_price"])/factor,json.dumps(before),json.dumps(after)))
                    cur.execute("UPDATE positions SET average_price=average_price/%s WHERE id=%s", (factor,position["id"]))
                    for lot in lots:
                        cur.execute("UPDATE position_lots SET unit_cost=unit_cost/%s WHERE id=%s", (factor,lot["id"]))
                adjusted += 1
            cur.execute("SELECT price FROM market_marks WHERE instrument_id=%s ORDER BY observed_at DESC,id DESC LIMIT 1 FOR UPDATE", (instrument_id,))
            underlying_mark = cur.fetchone()
            if underlying_mark:
                cur.execute("INSERT INTO market_marks (instrument_id,price,entered_by) VALUES (%s,%s,%s)", (instrument_id,Decimal(underlying_mark["price"])/factor,session["user_id"]))
        conn.commit(); flash(f"Corporate action recorded; {adjusted} option contracts adjusted.", "success")
    except Exception as exc:
        mysql_connection().rollback(); flash("Corporate action failed; no changes were saved.", "danger")
    finally:
        try: cur.close()
        except (UnboundLocalError, AttributeError): pass
    return redirect(url_for("web.corporate_actions"))
