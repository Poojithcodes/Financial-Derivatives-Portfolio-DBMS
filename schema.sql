CREATE DATABASE IF NOT EXISTS derivative_ledger CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE derivative_ledger;

CREATE TABLE IF NOT EXISTS users (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  full_name VARCHAR(120) NOT NULL,
  email VARCHAR(190) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('TRADER','ADMINISTRATOR','AUDITOR') NOT NULL DEFAULT 'TRADER',
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS trader_accounts (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  user_id BIGINT UNSIGNED NOT NULL,
  account_name VARCHAR(100) NOT NULL,
  currency CHAR(3) NOT NULL DEFAULT 'INR',
  cash_balance DECIMAL(20,2) NOT NULL DEFAULT 0,
  status ENUM('ACTIVE','SUSPENDED','CLOSED') NOT NULL DEFAULT 'ACTIVE',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_account_user FOREIGN KEY (user_id) REFERENCES users(id),
  INDEX ix_account_user (user_id, status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS instruments (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  symbol VARCHAR(32) NOT NULL UNIQUE,
  name VARCHAR(160) NOT NULL,
  instrument_type ENUM('STOCK','INDEX','ETF','OTHER') NOT NULL DEFAULT 'STOCK',
  exchange VARCHAR(40) NOT NULL DEFAULT 'NSE',
  currency CHAR(3) NOT NULL DEFAULT 'INR',
  status ENUM('ACTIVE','INACTIVE') NOT NULL DEFAULT 'ACTIVE'
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS option_contracts (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  instrument_id BIGINT UNSIGNED NOT NULL,
  symbol VARCHAR(64) NOT NULL UNIQUE,
  option_type ENUM('CALL','PUT') NOT NULL,
  strike_price DECIMAL(20,4) NOT NULL,
  expiry_date DATE NOT NULL,
  lot_size INT UNSIGNED NOT NULL,
  premium DECIMAL(20,4) NOT NULL DEFAULT 0,
  status ENUM('ACTIVE','EXPIRED','INACTIVE') NOT NULL DEFAULT 'ACTIVE',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_contract_instrument FOREIGN KEY (instrument_id) REFERENCES instruments(id),
  CONSTRAINT ck_contract_strike CHECK (strike_price > 0),
  CONSTRAINT ck_contract_lot CHECK (lot_size > 0),
  INDEX ix_contract_lookup (instrument_id, expiry_date, option_type, strike_price)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS market_marks (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  instrument_id BIGINT UNSIGNED NOT NULL,
  price DECIMAL(20,4) NOT NULL,
  observed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  entered_by BIGINT UNSIGNED NOT NULL,
  CONSTRAINT fk_mark_instrument FOREIGN KEY (instrument_id) REFERENCES instruments(id),
  CONSTRAINT fk_mark_user FOREIGN KEY (entered_by) REFERENCES users(id),
  INDEX ix_mark_latest (instrument_id, observed_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS option_marks (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  contract_id BIGINT UNSIGNED NOT NULL,
  price DECIMAL(20,4) NOT NULL,
  observed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  entered_by BIGINT UNSIGNED NOT NULL,
  CONSTRAINT fk_option_mark_contract FOREIGN KEY (contract_id) REFERENCES option_contracts(id),
  CONSTRAINT fk_option_mark_user FOREIGN KEY (entered_by) REFERENCES users(id),
  INDEX ix_option_mark_latest (contract_id, observed_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS orders (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  account_id BIGINT UNSIGNED NOT NULL,
  contract_id BIGINT UNSIGNED NOT NULL,
  side ENUM('BUY','SELL') NOT NULL,
  quantity INT UNSIGNED NOT NULL,
  order_type ENUM('MARKET','LIMIT') NOT NULL DEFAULT 'MARKET',
  limit_price DECIMAL(20,4) NULL,
  status ENUM('OPEN','EXECUTED','CANCELLED','REJECTED') NOT NULL DEFAULT 'OPEN',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_order_account FOREIGN KEY (account_id) REFERENCES trader_accounts(id),
  CONSTRAINT fk_order_contract FOREIGN KEY (contract_id) REFERENCES option_contracts(id),
  CONSTRAINT ck_order_quantity CHECK (quantity > 0),
  INDEX ix_order_account_date (account_id, created_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS trades (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  order_id BIGINT UNSIGNED NULL,
  account_id BIGINT UNSIGNED NOT NULL,
  contract_id BIGINT UNSIGNED NOT NULL,
  side ENUM('BUY','SELL') NOT NULL,
  quantity INT UNSIGNED NOT NULL,
  price DECIMAL(20,4) NOT NULL,
  fees DECIMAL(20,2) NOT NULL DEFAULT 0,
  executed_at DATETIME NOT NULL,
  CONSTRAINT fk_trade_order FOREIGN KEY (order_id) REFERENCES orders(id),
  CONSTRAINT fk_trade_account FOREIGN KEY (account_id) REFERENCES trader_accounts(id),
  CONSTRAINT fk_trade_contract FOREIGN KEY (contract_id) REFERENCES option_contracts(id),
  CONSTRAINT ck_trade_quantity CHECK (quantity > 0),
  CONSTRAINT ck_trade_price CHECK (price > 0),
  INDEX ix_trade_account_date (account_id, executed_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS positions (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  account_id BIGINT UNSIGNED NOT NULL,
  contract_id BIGINT UNSIGNED NOT NULL,
  net_quantity INT NOT NULL DEFAULT 0,
  average_price DECIMAL(20,4) NOT NULL DEFAULT 0,
  realized_pnl DECIMAL(20,2) NOT NULL DEFAULT 0,
  status ENUM('OPEN','CLOSED') NOT NULL DEFAULT 'OPEN',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_position_account FOREIGN KEY (account_id) REFERENCES trader_accounts(id),
  CONSTRAINT fk_position_contract FOREIGN KEY (contract_id) REFERENCES option_contracts(id),
  INDEX ix_position_account_status (account_id, status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS position_lots (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  position_id BIGINT UNSIGNED NOT NULL,
  opening_trade_id BIGINT UNSIGNED NOT NULL,
  direction ENUM('LONG','SHORT') NOT NULL,
  original_quantity INT UNSIGNED NOT NULL,
  remaining_quantity INT UNSIGNED NOT NULL,
  unit_cost DECIMAL(20,4) NOT NULL,
  opened_at DATETIME NOT NULL,
  CONSTRAINT fk_lot_position FOREIGN KEY (position_id) REFERENCES positions(id),
  CONSTRAINT fk_lot_trade FOREIGN KEY (opening_trade_id) REFERENCES trades(id),
  INDEX ix_lot_fifo (position_id, direction, opened_at, id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS margin_accounts (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  account_id BIGINT UNSIGNED NOT NULL UNIQUE,
  available_margin DECIMAL(20,2) NOT NULL DEFAULT 0,
  required_margin DECIMAL(20,2) NOT NULL DEFAULT 0,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_margin_account FOREIGN KEY (account_id) REFERENCES trader_accounts(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS margin_requirements (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  account_id BIGINT UNSIGNED NOT NULL,
  position_id BIGINT UNSIGNED NULL,
  required_amount DECIMAL(20,2) NOT NULL,
  calculated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_req_account FOREIGN KEY (account_id) REFERENCES trader_accounts(id),
  CONSTRAINT fk_req_position FOREIGN KEY (position_id) REFERENCES positions(id),
  INDEX ix_margin_history (account_id, calculated_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS margin_calls (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  account_id BIGINT UNSIGNED NOT NULL,
  required_amount DECIMAL(20,2) NOT NULL,
  available_amount DECIMAL(20,2) NOT NULL,
  status ENUM('OPEN','RESOLVED') NOT NULL DEFAULT 'OPEN',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at DATETIME NULL,
  CONSTRAINT fk_call_account FOREIGN KEY (account_id) REFERENCES trader_accounts(id),
  INDEX ix_call_status (account_id, status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS financial_transactions (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  account_id BIGINT UNSIGNED NOT NULL,
  trade_id BIGINT UNSIGNED NULL,
  transaction_type ENUM('TRADE','MARGIN','SETTLEMENT','DEPOSIT','WITHDRAWAL','ADJUSTMENT') NOT NULL,
  amount DECIMAL(20,2) NOT NULL,
  description VARCHAR(255) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_tx_account FOREIGN KEY (account_id) REFERENCES trader_accounts(id),
  CONSTRAINT fk_tx_trade FOREIGN KEY (trade_id) REFERENCES trades(id),
  INDEX ix_tx_history (account_id, created_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS settlement_events (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  position_id BIGINT UNSIGNED NOT NULL,
  account_id BIGINT UNSIGNED NOT NULL,
  settlement_price DECIMAL(20,4) NOT NULL,
  settlement_value DECIMAL(20,2) NOT NULL,
  status ENUM('PENDING_AUDIT','COMPLETE') NOT NULL DEFAULT 'PENDING_AUDIT',
  settled_by BIGINT UNSIGNED NOT NULL,
  notes VARCHAR(500) NOT NULL DEFAULT '',
  audit_hash CHAR(64) NULL,
  settled_at DATETIME NOT NULL,
  CONSTRAINT fk_settlement_position FOREIGN KEY (position_id) REFERENCES positions(id),
  CONSTRAINT fk_settlement_account FOREIGN KEY (account_id) REFERENCES trader_accounts(id),
  CONSTRAINT fk_settlement_user FOREIGN KEY (settled_by) REFERENCES users(id),
  INDEX ix_settlement_account_date (account_id, settled_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS audit_outbox (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  settlement_id BIGINT UNSIGNED NOT NULL UNIQUE,
  payload_json JSON NOT NULL,
  status ENUM('PENDING','COMPLETE') NOT NULL DEFAULT 'PENDING',
  mongo_hash CHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME NULL,
  CONSTRAINT fk_outbox_settlement FOREIGN KEY (settlement_id) REFERENCES settlement_events(id),
  INDEX ix_outbox_status (status, id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS corporate_actions (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  instrument_id BIGINT UNSIGNED NOT NULL,
  action_type ENUM('SPLIT','REVERSE_SPLIT','BONUS','SPECIAL_DIVIDEND') NOT NULL,
  ratio_num DECIMAL(12,6) NOT NULL DEFAULT 1,
  ratio_den DECIMAL(12,6) NOT NULL DEFAULT 1,
  cash_per_share DECIMAL(20,4) NOT NULL DEFAULT 0,
  effective_date DATE NOT NULL,
  created_by BIGINT UNSIGNED NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_action_instrument FOREIGN KEY (instrument_id) REFERENCES instruments(id),
  CONSTRAINT fk_action_user FOREIGN KEY (created_by) REFERENCES users(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS contract_adjustments (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  corporate_action_id BIGINT UNSIGNED NOT NULL,
  contract_id BIGINT UNSIGNED NOT NULL,
  original_strike DECIMAL(20,4) NOT NULL,
  adjusted_strike DECIMAL(20,4) NOT NULL,
  original_lot_size INT UNSIGNED NOT NULL,
  adjusted_lot_size INT UNSIGNED NOT NULL,
  adjusted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_adjust_action FOREIGN KEY (corporate_action_id) REFERENCES corporate_actions(id),
  CONSTRAINT fk_adjust_contract FOREIGN KEY (contract_id) REFERENCES option_contracts(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS position_adjustments (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  corporate_action_id BIGINT UNSIGNED NOT NULL,
  position_id BIGINT UNSIGNED NOT NULL,
  quantity_before INT NOT NULL,
  quantity_after INT NOT NULL,
  average_price_before DECIMAL(20,4) NOT NULL,
  average_price_after DECIMAL(20,4) NOT NULL,
  lots_before JSON NOT NULL,
  lots_after JSON NOT NULL,
  adjusted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_position_adjust_action FOREIGN KEY (corporate_action_id) REFERENCES corporate_actions(id),
  CONSTRAINT fk_position_adjust_position FOREIGN KEY (position_id) REFERENCES positions(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS scenarios (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  account_id BIGINT UNSIGNED NOT NULL,
  name VARCHAR(120) NOT NULL,
  description VARCHAR(500) NOT NULL DEFAULT '',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_scenario_account FOREIGN KEY (account_id) REFERENCES trader_accounts(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS scenario_trades (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  scenario_id BIGINT UNSIGNED NOT NULL,
  contract_id BIGINT UNSIGNED NOT NULL,
  side ENUM('BUY','SELL') NOT NULL,
  quantity INT UNSIGNED NOT NULL,
  price DECIMAL(20,4) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_scenario_trade FOREIGN KEY (scenario_id) REFERENCES scenarios(id) ON DELETE CASCADE,
  CONSTRAINT fk_scenario_contract FOREIGN KEY (contract_id) REFERENCES option_contracts(id)
) ENGINE=InnoDB;

CREATE OR REPLACE VIEW v_open_positions AS
SELECT p.id position_id, p.account_id, p.contract_id, c.symbol, c.option_type, c.strike_price, c.expiry_date,
       p.net_quantity, p.average_price, p.realized_pnl, p.status
FROM positions p JOIN option_contracts c ON c.id=p.contract_id WHERE p.status='OPEN';
