-- Mini CRM development hard reset.
-- The shell wrapper verifies APP_ENV, the Compose target, and database identity.
-- This file repeats the database-name/revision guard so it is not safe to run
-- accidentally against an arbitrary PostgreSQL database. The shell wrapper
-- supplies expected_revision from `alembic heads`, so this guard follows the
-- checked-out migration head instead of duplicating a stale revision literal.
BEGIN;

-- psql variables are expanded before SQL statements, not inside a dollar-quoted
-- PL/pgSQL body. Pass the dynamically detected head through this transaction-
-- local setting so the guard can read it without embedding a stale revision.
SET LOCAL p100.dev_reset_expected_revision = :'expected_revision';

DO $$
DECLARE
    expected_tables CONSTANT text[] := ARRAY[
        'alembic_version',
        'crm_areas',
        'crm_auth_invites',
        'crm_auth_sessions',
        'crm_deals',
        'crm_outbox',
        'crm_password_reset_tokens',
        'crm_projects',
        'crm_units',
        'crm_users'
    ];
    actual_tables text[];
    missing_tables text[];
    unexpected_tables text[];
    current_revision text;
BEGIN
    IF current_database() NOT IN ('minicrm', 'minicrm_dev', 'minicrm_test') THEN
        RAISE EXCEPTION 'refusing Mini CRM reset in database %', current_database();
    END IF;

    SELECT COALESCE(array_agg(table_name ORDER BY table_name), ARRAY[]::text[])
      INTO actual_tables
      FROM information_schema.tables
     WHERE table_schema = 'public' AND table_type = 'BASE TABLE';

    SELECT COALESCE(array_agg(name ORDER BY name), ARRAY[]::text[])
      INTO missing_tables
      FROM unnest(expected_tables) AS required(name)
     WHERE NOT (name = ANY(actual_tables));

    SELECT COALESCE(array_agg(name ORDER BY name), ARRAY[]::text[])
      INTO unexpected_tables
      FROM unnest(actual_tables) AS present(name)
     WHERE NOT (name = ANY(expected_tables));

    IF cardinality(missing_tables) > 0 THEN
        RAISE EXCEPTION 'refusing Mini CRM reset; missing classified tables: %', missing_tables;
    END IF;
    IF cardinality(unexpected_tables) > 0 THEN
        RAISE EXCEPTION 'refusing Mini CRM reset; unclassified public tables: %', unexpected_tables;
    END IF;

    IF (SELECT count(*) FROM alembic_version) <> 1 THEN
        RAISE EXCEPTION 'refusing Mini CRM reset; alembic_version is not singular';
    END IF;
    SELECT version_num INTO current_revision FROM alembic_version;
    IF current_revision <> current_setting('p100.dev_reset_expected_revision') THEN
        RAISE EXCEPTION 'refusing Mini CRM reset; expected revision %, got %',
            current_setting('p100.dev_reset_expected_revision'), current_revision;
    END IF;
END;
$$;

-- All current domain, outbox, and local-auth rows are removed.  No CASCADE is
-- used: every current FK table is listed explicitly, so a newly-added
-- dependency stops this statement instead of broadening the deletion.
TRUNCATE TABLE
    crm_auth_sessions,
    crm_password_reset_tokens,
    crm_auth_invites,
    crm_outbox,
    crm_deals,
    crm_units,
    crm_areas,
    crm_projects,
    crm_users;

COMMIT;
