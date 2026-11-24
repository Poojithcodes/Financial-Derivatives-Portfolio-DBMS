USE derivative_ledger;

CREATE TABLE IF NOT EXISTS kite_sessions (
  user_id BIGINT UNSIGNED PRIMARY KEY,
  kite_user_id VARCHAR(64) NOT NULL,
  kite_user_name VARCHAR(160) NOT NULL,
  access_token_ciphertext VARBINARY(512) NOT NULL,
  connected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_kite_session_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;
