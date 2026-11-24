"""Business operations. All monetary arithmetic uses Decimal and SQL transactions."""
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime, timezone
import hashlib
import json
from .db import mysql_connection, mongo_database


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _calculate_margin_cursor(cur, account_id):
    cur.execute("SELECT p.id,p.net_quantity,c.lot_size,c.strike_price,COALESCE((SELECT om.price FROM option_marks om WHERE om.contract_id=c.id ORDER BY om.observed_at DESC,om.id DESC LIMIT 1),c.premium) premium,c.option_type,i.id instrument_id,(SELECT m.price FROM market_marks m WHERE m.instrument_id=i.id ORDER BY m.observed_at DESC,m.id DESC LIMIT 1) underlying_mark FROM positions p JOIN option_contracts c ON c.id=p.contract_id JOIN instruments i ON i.id=c.instrument_id WHERE p.account_id=%s AND p.status='OPEN' AND p.net_quantity<0", (account_id,))
    positions = cur.fetchall()
    total = Decimal("0")
    for p in positions:
        if p["underlying_mark"] is None:
            raise ValueError(f"Enter an underlying market mark for instrument #{p['instrument_id']} before calculating margin.")
        spot, strike = Decimal(p["underlying_mark"]), Decimal(p["strike_price"])
        otm = max(Decimal(0), strike-spot) if p["option_type"] == "CALL" else max(Decimal(0), spot-strike)
        per_unit = Decimal(p["premium"]) + max(spot*Decimal("0.20")-otm, spot*Decimal("0.10"))
        total += per_unit * abs(int(p["net_quantity"])) * int(p["lot_size"])
    total = money(total)
    cur.execute("SELECT cash_balance FROM trader_accounts WHERE id=%s FOR UPDATE", (account_id,))
    account = cur.fetchone()
    if not account:
        raise ValueError("Trading account not found.")
    available = money(account["cash_balance"])
    cur.execute("INSERT INTO margin_accounts (account_id,available_margin,required_margin) VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE available_margin=VALUES(available_margin),required_margin=VALUES(required_margin)", (account_id,available,total))
    cur.execute("INSERT INTO margin_requirements (account_id,required_amount) VALUES (%s,%s)", (account_id,total))
    cur.execute("SELECT id FROM margin_calls WHERE account_id=%s AND status='OPEN' FOR UPDATE", (account_id,))
    open_call = cur.fetchone()
    if available < total and open_call:
        cur.execute("UPDATE margin_calls SET required_amount=%s,available_amount=%s WHERE id=%s", (total,available,open_call["id"]))
    elif available < total:
        cur.execute("INSERT INTO margin_calls (account_id,required_amount,available_amount,status) VALUES (%s,%s,%s,'OPEN')", (account_id,total,available))
    elif open_call:
        cur.execute("UPDATE margin_calls SET status='RESOLVED',resolved_at=UTC_TIMESTAMP() WHERE account_id=%s AND status='OPEN'", (account_id,))
    return {"required": str(total), "available": str(available), "call": available < total}


def calculate_margin(account_id):
    """Recalculate indicative short-option margin using latest manually entered marks.

    Estimate per underlying unit: premium + max(20% underlying - OTM amount, 10% underlying).
    This explicit project assumption is not an exchange/broker margin model.
    """
    connection = mysql_connection()
    cur = connection.cursor(dictionary=True)
    try:
        result = _calculate_margin_cursor(cur, account_id)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        cur.close()


def execute_trade(account_id, contract_id, side, quantity, price, fees=0, order_id=None):
    """Execute a trade, update FIFO lots/position/cash/margin atomically in MySQL."""
    if side not in {"BUY", "SELL"} or int(quantity) <= 0 or money(price) <= 0:
        raise ValueError("Trade side, quantity, or price is invalid.")
    qty, price, fees = int(quantity), money(price), money(fees)
    connection = mysql_connection()
    cur = connection.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, lot_size FROM option_contracts WHERE id=%s AND status='ACTIVE' FOR UPDATE", (contract_id,))
        contract = cur.fetchone()
        if not contract:
            raise ValueError("Active option contract not found.")
        cur.execute("SELECT id, cash_balance FROM trader_accounts WHERE id=%s AND status='ACTIVE' FOR UPDATE", (account_id,))
        account = cur.fetchone()
        if not account:
            raise ValueError("Active trading account not found.")
        cur.execute("SELECT id, net_quantity FROM positions WHERE account_id=%s AND contract_id=%s AND status='OPEN' FOR UPDATE", (account_id, contract_id))
        position = cur.fetchone()
        signed = qty if side == "BUY" else -qty
        if position and position["net_quantity"] and (position["net_quantity"] > 0) != (signed > 0) and qty > abs(position["net_quantity"]):
            raise ValueError("A single trade cannot cross through zero; close the current position first.")
        lot_size = int(contract["lot_size"])
        notional = price * qty * lot_size
        unit_cost = price + (fees / Decimal(qty * lot_size)) * (1 if side == "BUY" else -1)
        cash_delta = -(notional + fees) if side == "BUY" else notional - fees
        if money(account["cash_balance"]) + cash_delta < 0:
            raise ValueError("Insufficient account cash for this trade.")
        cur.execute("INSERT INTO trades (order_id, account_id, contract_id, side, quantity, price, fees, executed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP())",
                    (order_id, account_id, contract_id, side, qty, price, fees))
        trade_id = cur.lastrowid
        if not position:
            cur.execute("INSERT INTO positions (account_id, contract_id, net_quantity, average_price, realized_pnl, status) VALUES (%s,%s,0,0,0,'OPEN')", (account_id, contract_id))
            position_id = cur.lastrowid
            old_qty, old_avg = 0, Decimal("0")
        else:
            position_id, old_qty, old_avg = position["id"], int(position["net_quantity"]), money(position["average_price"])
        realized = Decimal("0")
        remaining = qty
        # FIFO close against open lots with opposite direction.
        if old_qty and (old_qty > 0) != (signed > 0):
            cur.execute("SELECT id, remaining_quantity, unit_cost FROM position_lots WHERE position_id=%s AND remaining_quantity>0 AND direction=%s ORDER BY opened_at, id FOR UPDATE",
                        (position_id, "LONG" if old_qty > 0 else "SHORT"))
            for lot in cur.fetchall():
                if not remaining:
                    break
                used = min(remaining, int(lot["remaining_quantity"]))
                unit_cost = money(lot["unit_cost"])
                realized += (price - unit_cost) * used * lot_size * (1 if old_qty > 0 else -1)
                realized -= fees * Decimal(used) / Decimal(qty)
                cur.execute("UPDATE position_lots SET remaining_quantity=remaining_quantity-%s WHERE id=%s", (used, lot["id"]))
                remaining -= used
        new_qty = old_qty + signed
        if old_qty == 0 or (old_qty > 0) == (signed > 0):
            new_avg = ((abs(old_qty) * old_avg + qty * unit_cost) / (abs(old_qty) + qty)) if new_qty else Decimal("0")
        elif remaining:
            new_avg = unit_cost
        elif new_qty == 0:
            new_avg = Decimal("0")
        else:
            new_avg = old_avg
        if remaining:
            cur.execute("INSERT INTO position_lots (position_id, opening_trade_id, direction, original_quantity, remaining_quantity, unit_cost, opened_at) VALUES (%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP())",
                        (position_id, trade_id, "LONG" if signed > 0 else "SHORT", remaining, remaining, unit_cost))
        status = "CLOSED" if new_qty == 0 else "OPEN"
        cur.execute("UPDATE positions SET net_quantity=%s, average_price=%s, realized_pnl=realized_pnl+%s, status=%s, updated_at=UTC_TIMESTAMP() WHERE id=%s",
                    (new_qty, new_avg, realized, status, position_id))
        cur.execute("UPDATE trader_accounts SET cash_balance=cash_balance+%s, updated_at=UTC_TIMESTAMP() WHERE id=%s", (cash_delta, account_id))
        cur.execute("INSERT INTO financial_transactions (account_id, trade_id, transaction_type, amount, description, created_at) VALUES (%s,%s,'TRADE',%s,%s,UTC_TIMESTAMP())",
                    (account_id, trade_id, cash_delta, f"{side} {qty} contract(s)"))
        _calculate_margin_cursor(cur, account_id)
        connection.commit()
        return {"trade_id": trade_id, "position_id": position_id, "realized_pnl": str(money(realized)), "net_quantity": new_qty}
    except Exception:
        connection.rollback()
        raise
    finally:
        cur.close()


def record_settlement(position_id, actor_id, settlement_price, notes=""):
    """Close position and enqueue tamper-evident audit record in one SQL transaction."""
    price = money(settlement_price)
    connection = mysql_connection()
    cur = connection.cursor(dictionary=True)
    try:
        cur.execute("SELECT p.*, c.lot_size, c.strike_price, c.option_type, a.id account_id FROM positions p JOIN option_contracts c ON c.id=p.contract_id JOIN trader_accounts a ON a.id=p.account_id WHERE p.id=%s FOR UPDATE", (position_id,))
        p = cur.fetchone()
        if not p or p["status"] != "OPEN":
            raise ValueError("Open position not found.")
        quantity = abs(int(p["net_quantity"]))
        intrinsic = max(Decimal("0"), price - money(p["strike_price"])) if p["option_type"] == "CALL" else max(Decimal("0"), money(p["strike_price"]) - price)
        value = money(intrinsic * quantity * int(p["lot_size"]))
        direction = 1 if int(p["net_quantity"]) > 0 else -1
        realized = money((intrinsic - money(p["average_price"])) * quantity * int(p["lot_size"]) * direction)
        cur.execute("INSERT INTO settlement_events (position_id, account_id, settlement_price, settlement_value, status, settled_by, notes, settled_at) VALUES (%s,%s,%s,%s,'PENDING_AUDIT',%s,%s,UTC_TIMESTAMP())",
                    (position_id, p["account_id"], price, value, actor_id, notes))
        settlement_id = cur.lastrowid
        cur.execute("INSERT INTO financial_transactions (account_id, transaction_type, amount, description, created_at) VALUES (%s,'SETTLEMENT',%s,%s,UTC_TIMESTAMP())",
                    (p["account_id"], value * direction, f"Settlement #{settlement_id}"))
        cur.execute("UPDATE trader_accounts SET cash_balance=cash_balance+%s WHERE id=%s", (value * direction, p["account_id"]))
        cur.execute("UPDATE positions SET status='CLOSED', net_quantity=0, realized_pnl=realized_pnl+%s, updated_at=UTC_TIMESTAMP() WHERE id=%s", (realized, position_id))
        cur.execute("UPDATE position_lots SET remaining_quantity=0 WHERE position_id=%s", (position_id,))
        payload = {"settlement_id": settlement_id, "position_id": position_id, "account_id": p["account_id"], "signed_quantity": int(p["net_quantity"]),
                   "settlement_price": str(price), "settlement_value": str(value), "realized_pnl": str(realized), "settled_at": datetime.now(timezone.utc).isoformat()}
        cur.execute("INSERT INTO audit_outbox (settlement_id, payload_json, status) VALUES (%s,%s,'PENDING')", (settlement_id, json.dumps(payload, sort_keys=True)))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cur.close()
    # Audit persistence is retryable: SQL settlement remains visible as pending if MongoDB is down.
    try:
        flush_audit_outbox(limit=1)
    except Exception:
        pass
    return settlement_id


def flush_audit_outbox(limit=100):
    """Idempotently append pending settlements to MongoDB hash chain, then mark SQL rows."""
    connection = mysql_connection()
    cur = connection.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, settlement_id, payload_json FROM audit_outbox WHERE status='PENDING' ORDER BY id LIMIT %s", (int(limit),))
        records = cur.fetchall()
        if not records:
            return 0
        collection = mongo_database()["settlement_blocks"]
        collection.create_index("settlement_id", unique=True)
        for row in records:
            payload = json.loads(row["payload_json"])
            existing = collection.find_one({"settlement_id": row["settlement_id"]})
            if existing:
                digest = existing["hash"]
                cur.execute("UPDATE audit_outbox SET status='COMPLETE', completed_at=UTC_TIMESTAMP(), mongo_hash=%s WHERE id=%s", (digest, row["id"]))
                cur.execute("UPDATE settlement_events SET status='COMPLETE', audit_hash=%s WHERE id=%s", (digest, row["settlement_id"]))
                connection.commit()
                continue
            previous = collection.find_one(sort=[("sequence", -1)])
            prev_hash = previous["hash"] if previous else "0" * 64
            sequence = int(previous["sequence"] + 1) if previous else 1
            canonical = json.dumps({"sequence": sequence, "previous_hash": prev_hash, "payload": payload}, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            collection.update_one({"settlement_id": row["settlement_id"]}, {"$setOnInsert": {
                "settlement_id": row["settlement_id"], "sequence": sequence, "previous_hash": prev_hash,
                "payload": payload, "hash": digest, "created_at": datetime.now(timezone.utc)}}, upsert=True)
            cur.execute("UPDATE audit_outbox SET status='COMPLETE', completed_at=UTC_TIMESTAMP(), mongo_hash=%s WHERE id=%s", (digest, row["id"]))
            cur.execute("UPDATE settlement_events SET status='COMPLETE', audit_hash=%s WHERE id=%s", (digest, row["settlement_id"]))
            connection.commit()
        return len(records)
    except Exception:
        connection.rollback()
        raise
    finally:
        cur.close()


def verify_audit_chain():
    collection = mongo_database()["settlement_blocks"]
    previous = "0" * 64
    count = 0
    for doc in collection.find().sort("sequence", 1):
        canonical = json.dumps({"sequence": doc["sequence"], "previous_hash": doc["previous_hash"], "payload": doc["payload"]}, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if doc["previous_hash"] != previous or doc["hash"] != digest:
            return {"valid": False, "checked": count, "failed_sequence": doc["sequence"]}
        previous, count = digest, count + 1
    return {"valid": True, "checked": count}
