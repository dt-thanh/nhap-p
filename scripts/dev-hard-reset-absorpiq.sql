-- AbsorpIQ development hard reset.
-- `alembic_version` is migration history. `sync_credentials` is the active
-- Mini CRM service-credential registry. `ranking_configs` is versioned,
-- governed scoring configuration: migrations seed it once, so truncating it
-- makes every later sync-triggered ranking fail with NO_ACTIVE_CONFIG.
-- All three are deliberately preserved.
BEGIN;

DO $$
DECLARE
    expected_tables CONSTANT text[] := ARRAY[
        'absorption_daily',
        'agent_executions',
        'agent_recommendations',
        'alembic_version',
        'alerts',
        'approvals',
        'areas',
        'audit_logs',
        'calculator_comparisons',
        'crm_source_records',
        'deal_status_history',
        'deals',
        'expert_profiles',
        'explanations',
        'feature_snapshots',
        'forecast_jobs',
        'forecast_points',
        'forecasts',
        'inventory_snapshots',
        'llm_calls',
        'project_price_observations',
        'projects',
        'proposals',
        'ranking_config_audit_events',
        'ranking_config_features',
        'ranking_configs',
        'ranking_evidence_document_chunks',
        'ranking_evidence_document_features',
        'ranking_evidence_documents',
        'ranking_evidence_extraction_attempts',
        'ranking_explanations',
        'ranking_feature_definitions',
        'ranking_feature_justifications',
        'ranking_feature_lineage',
        'ranking_feature_snapshots',
        'ranking_feature_values',
        'ranking_proposal_reviews',
        'ranking_runs',
        'ranking_scores',
        'ranking_weight_proposals',
        'reconciliation_findings',
        'reconciliation_runs',
        'refresh_tokens',
        'sales_campaign_units',
        'sales_campaigns',
        'sales_records',
        'settings',
        'suggestions',
        'sync_credentials',
        'sync_payloads',
        'unit_status_history',
        'units',
        'upload_errors',
        'upload_files',
        'user_areas',
        'users'
    ];
    actual_tables text[];
    missing_tables text[];
    unexpected_tables text[];
    current_revision text;
BEGIN
    IF current_database() NOT IN ('absorption', 'absorption_dev', 'absorption_test') THEN
        RAISE EXCEPTION 'refusing AbsorpIQ reset in database %', current_database();
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
        RAISE EXCEPTION 'refusing AbsorpIQ reset; missing classified tables: %', missing_tables;
    END IF;
    IF cardinality(unexpected_tables) > 0 THEN
        RAISE EXCEPTION 'refusing AbsorpIQ reset; unclassified public tables: %', unexpected_tables;
    END IF;

    IF (SELECT count(*) FROM alembic_version) <> 1 THEN
        RAISE EXCEPTION 'refusing AbsorpIQ reset; alembic_version is not singular';
    END IF;
    SELECT version_num INTO current_revision FROM alembic_version;
    IF current_revision <> '0036_remove_historical_ranking' THEN
        RAISE EXCEPTION 'refusing AbsorpIQ reset; expected revision 0036_remove_historical_ranking, got %', current_revision;
    END IF;
END;
$$;

-- This includes domain, ingestion, forecast/ranking, audit, and local-auth
-- rows.  `alembic_version`, `sync_credentials`, and `ranking_configs` are
-- intentionally absent.  A reset clears derived scores/runs but must retain
-- the approved scoring policy those runs reference.
-- No CASCADE is used; an FK introduced by a future migration fails closed.
TRUNCATE TABLE
    absorption_daily,
    agent_executions,
    agent_recommendations,
    alerts,
    approvals,
    areas,
    audit_logs,
    calculator_comparisons,
    crm_source_records,
    deal_status_history,
    deals,
    expert_profiles,
    explanations,
    feature_snapshots,
    forecast_jobs,
    forecast_points,
    forecasts,
    inventory_snapshots,
    llm_calls,
    project_price_observations,
    projects,
    proposals,
    ranking_config_audit_events,
    ranking_config_features,
    ranking_evidence_document_chunks,
    ranking_evidence_document_features,
    ranking_evidence_documents,
    ranking_evidence_extraction_attempts,
    ranking_explanations,
    ranking_feature_definitions,
    ranking_feature_justifications,
    ranking_feature_lineage,
    ranking_feature_snapshots,
    ranking_feature_values,
    ranking_proposal_reviews,
    ranking_runs,
    ranking_scores,
    ranking_weight_proposals,
    reconciliation_findings,
    reconciliation_runs,
    refresh_tokens,
    sales_campaign_units,
    sales_campaigns,
    sales_records,
    settings,
    suggestions,
    sync_payloads,
    unit_status_history,
    units,
    upload_errors,
    upload_files,
    user_areas,
    users;

COMMIT;
