# DerivativeLedger

A local portfolio and settlement ledger for options and derivatives. Operational records live in MySQL. Completed-settlement audit documents are mirrored to MongoDB as a SHA-256 hash chain.

## Start locally (Windows PowerShell)

1. Install Python 3.11 or newer and Docker Desktop with Docker Compose.
2. Create a virtual environment and install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   py -m pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

3. Set a private `SECRET_KEY` and database passwords in `.env`. The example values are for local development only.
4. Start the database services:

   ```powershell
   docker compose up -d
   ```

   MySQL initializes from `schema.sql` on the first start. MongoDB stores the audit chain. The app can run while MongoDB is unavailable; settlement audit writes remain pending in MySQL and can be retried from the Audit page after MongoDB returns.

5. Create the first administrator and start Flask:

   ```powershell
   py scripts/create_admin.py
   flask --app main run
   ```

   Visit <http://127.0.0.1:5000>. Use `/health` to check database connectivity. The health endpoint requires both databases.

## Configure Kite Connect login

Kite authentication is implemented as a server-side login flow. In the Zerodha Kite Connect developer console, create/open your app and set its **Redirect URL** to exactly:

```text
http://127.0.0.1:5000/kite/callback
```

Add the API key and secret issued for that app to `.env`. Generate a Fernet key for encrypting access tokens at rest:

```powershell
py -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the resulting values in `.env`:

```dotenv
KITE_API_KEY=your_api_key
KITE_API_SECRET=your_api_secret
KITE_TOKEN_ENCRYPTION_KEY=your_generated_fernet_key
KITE_REDIRECT_URL=http://127.0.0.1:5000/kite/callback
```

Keep the API secret and encryption key private. Back up the encryption key securely; losing it prevents decryption of stored access tokens. Restart Flask, open the app at <http://127.0.0.1:5000> (use this exact host), sign in, open **Kite Connect**, and select **Connect to Kite**. Zerodha redirects to the callback, where the server exchanges the one-time request token and stores the encrypted access token. Kite access tokens expire daily, so reconnect when needed.

For an existing database created before Kite support, run the migration once in PowerShell:

```powershell
docker compose exec mysql mysql -u root -p derivative_ledger
```

Enter the MySQL root password from `.env`, then at the `mysql>` prompt run:

```sql
SOURCE /docker-entrypoint-initdb.d/002-kite-sessions.sql;
EXIT;
```

The callback URL is for local development. For a deployed app, use its public HTTPS URL, set that same exact value as `KITE_REDIRECT_URL`, register it with Zerodha, and serve the callback over HTTPS.

`docker compose down` stops the database containers. Named volumes preserve their data. To delete local database data, run `docker compose down -v` only when you intend to remove both database volumes.

## Feature checklist

- [x] Registration, login, salted password hashing, and Trader / Administrator / Auditor roles.
- [x] Trader accounts, instruments, and option contract reference data.
- [x] Order records and manual market execution entry, with account cash checks.
- [x] Position aggregation and FIFO lot-level realized P&L on trade closures.
- [x] Margin estimates, margin call records, and manually maintained underlying marks.
- [x] Position closure with option intrinsic-value settlement and financial transaction history.
- [x] Settlement audit outbox, MongoDB hash-chain writes, verification, and retry screen.
- [x] Portfolio views, account history, summary reports, and FIFO as-of reconstruction using recorded marks.
- [x] Scenario records with live versus projected P&L, premium exposure, and short-margin estimates.
- [x] Split, reverse-split, and bonus adjustments to contracts, open position basis, and FIFO lots, with before/after records.
- [x] Session form tokens for POST requests.
- [x] Kite Connect login redirect, request-token exchange, callback-state validation, encrypted access-token storage, and disconnect/revoke.
- [ ] Automated background retry worker and concurrency-safe audit chain appender for multi-user deployment.
- [ ] Password reset, account recovery, and production deployment hardening.

## Financial and audit assumptions

- All prices and cash amounts are decimal values. Option contract quantity means number of contracts; notional and settlement amounts multiply by the contract lot size.
- The margin estimate is intentionally an educational approximation: for a short option it uses premium plus the greater of 20% of underlying price less out-of-the-money value, or 10% of underlying price. It is not an exchange or broker margin model. An Administrator must enter marks; no live market data feed or broker connection is present in the specification. Short-option trades require an underlying mark, and every execution recalculates margin within the MySQL transaction.
- Settlement assumes the entered price is the underlying price at expiry/closure and computes intrinsic value. This first implementation credits/debits intrinsic value and records realized P&L; it does not model exercise fees, early exercise, assignment, or cash-settled index-specific conventions.
- The MongoDB chain is tamper-evident when verified, but it is not a decentralized blockchain. A privileged operator able to rewrite all MongoDB documents can rebuild a chain; independent backups or external anchoring would be required for stronger guarantees.
- SQL commits the settlement and outbox row atomically. MongoDB mirroring is retryable, not part of a distributed transaction; the settlement status stays `PENDING_AUDIT` until mirrored.
- Split, reverse-split, and bonus actions adjust current option terms, open FIFO lot basis, and position average cost while retaining before/after records. Special dividends are recorded for audit but are not automatically applied to option terms because the specification does not define an exchange-specific adjustment rule.
- Historical reconstruction uses recorded option/underlying marks and recorded split/bonus actions. If no option mark was recorded by the selected date, unrealized P&L is shown as unavailable; margin estimates require an underlying mark.

## Structure

```text
derivativeledger/       Flask app, routes, DB access, security and business services
derivativeledger/templates/  Server-rendered Bootstrap UI
derivativeledger/static/     Local application styling
schema.sql              MySQL schema, keys, constraints, indexes and portfolio view
scripts/create_admin.py First privileged user bootstrap
docker-compose.yml      Local MySQL and MongoDB services
```

Bootstrap CSS and JavaScript are loaded from jsDelivr; application data and database operation are local, but the Bootstrap styling bundle requires internet access unless you vendor those assets locally.
