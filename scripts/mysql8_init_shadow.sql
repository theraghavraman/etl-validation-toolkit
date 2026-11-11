-- For the disposable, loopback-only mysql8-staging Docker service. Never production.
CREATE DATABASE IF NOT EXISTS etlguard_shadow_ci CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'etlguard_shadow'@'%' IDENTIFIED BY 'etlguard_shadow_local_only';
GRANT ALL PRIVILEGES ON etlguard_shadow_ci.* TO 'etlguard_shadow'@'%';
