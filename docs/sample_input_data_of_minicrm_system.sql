-- Sample input data for the Mini CRM database (schema head: 0004_outbox_hierarchy_entities).
-- Hierarchy: crm_projects -> crm_areas -> crm_units -> crm_deals.
-- mirrored_at / mirrored_revision / last_sync_batch_id are left NULL throughout
-- (not yet synced to the backend), so this data is immediately usable to drive
-- the outbox/relay pipeline. No PII, no prices, no commissions.

BEGIN;

-- =============================================================================
-- Projects
-- =============================================================================

INSERT INTO crm_projects
    (id, external_id, name, launch_date, status, source_revision, created_at, updated_at,
     mirrored_at, mirrored_revision, last_sync_batch_id)
VALUES
    ('a1000000-0000-0000-0000-000000000001', 'P-0001', 'Riverside Gardens',   '2026-06-01', 'active',   1,
     '2026-01-10 09:00:00+07', '2026-01-10 09:00:00+07', NULL, NULL, NULL),

    ('a1000000-0000-0000-0000-000000000002', 'P-0002', 'Skyline Residences',  '2026-09-15', 'active',   1,
     '2026-01-12 09:00:00+07', '2026-01-12 09:00:00+07', NULL, NULL, NULL),

    -- Edge case: archived project that still has live (non-deleted) children
    -- below (area A-0004, units U-0018..U-0023) — used to test that the
    -- ingestion pipeline rejects/flags new writes under an archived parent.
    ('a1000000-0000-0000-0000-000000000003', 'P-0003', 'Old Harbor Complex',  '2025-12-01', 'archived', 2,
     '2025-11-01 09:00:00+07', '2026-06-01 14:00:00+07', NULL, NULL, NULL);

-- =============================================================================
-- Areas
-- =============================================================================

INSERT INTO crm_areas
    (id, external_id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units,
     status, source_revision, created_at, updated_at, mirrored_at, mirrored_revision, last_sync_batch_id)
VALUES
    ('b2000000-0000-0000-0000-000000000001', 'A-0001', 'a1000000-0000-0000-0000-000000000001',
     'North Tower', '2PN', 2, 65.5, 9, 'active', 1,
     '2026-01-11 10:00:00+07', '2026-01-11 10:00:00+07', NULL, NULL, NULL),

    ('b2000000-0000-0000-0000-000000000002', 'A-0002', 'a1000000-0000-0000-0000-000000000001',
     'South Tower', '3PN', 3, 85.0, 8, 'active', 1,
     '2026-01-11 10:30:00+07', '2026-01-11 10:30:00+07', NULL, NULL, NULL),

    -- Edge case: area with zero live units (planned, not yet built out in the CRM).
    ('b2000000-0000-0000-0000-000000000003', 'A-0003', 'a1000000-0000-0000-0000-000000000002',
     'Sunrise Block', 'Studio', 0, 32.0, 10, 'active', 1,
     '2026-01-13 10:00:00+07', '2026-01-13 10:00:00+07', NULL, NULL, NULL),

    -- Area itself is still 'active'; only its parent project (P-0003) is archived —
    -- this is the "live children under an archived project" edge case.
    ('b2000000-0000-0000-0000-000000000004', 'A-0004', 'a1000000-0000-0000-0000-000000000003',
     'Harbor Block', '2PN', 2, 68.0, 6, 'active', 1,
     '2025-11-02 10:00:00+07', '2025-11-02 10:00:00+07', NULL, NULL, NULL);

-- =============================================================================
-- Units
-- =============================================================================

INSERT INTO crm_units
    (id, external_id, area_id, area_name, unit_type, unit_code, unit_status, source_revision,
     deleted_at, created_at, updated_at, mirrored_at, mirrored_revision, last_sync_batch_id)
VALUES
    -- North Tower (A-0001, North Tower / 2PN, project P-0001)
    (gen_random_uuid(), 'U-0001', 'b2000000-0000-0000-0000-000000000001', 'North Tower', '2PN', 'A101', 'sold',     2, NULL,
     '2026-01-15 09:00:00+07', '2026-03-10 16:00:00+07', NULL, NULL, NULL), -- multi-deal unit, see Deals section
    (gen_random_uuid(), 'U-0002', 'b2000000-0000-0000-0000-000000000001', 'North Tower', '2PN', 'A102', 'reserved', 2, NULL,
     '2026-01-15 09:05:00+07', '2026-02-05 09:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0003', 'b2000000-0000-0000-0000-000000000001', 'North Tower', '2PN', 'A103', 'sold',     2, NULL,
     '2026-01-15 09:10:00+07', '2026-02-15 14:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0004', 'b2000000-0000-0000-0000-000000000001', 'North Tower', '2PN', 'A104', 'available',1, NULL,
     '2026-01-15 09:15:00+07', '2026-01-15 09:15:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0005', 'b2000000-0000-0000-0000-000000000001', 'North Tower', '2PN', 'A105', 'available',1, NULL,
     '2026-01-15 09:20:00+07', '2026-01-15 09:20:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0006', 'b2000000-0000-0000-0000-000000000001', 'North Tower', '2PN', 'A106', 'available',1, NULL,
     '2026-01-15 09:25:00+07', '2026-01-15 09:25:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0007', 'b2000000-0000-0000-0000-000000000001', 'North Tower', '2PN', 'A107', 'available',1, NULL,
     '2026-01-15 09:30:00+07', '2026-01-15 09:30:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0008', 'b2000000-0000-0000-0000-000000000001', 'North Tower', '2PN', 'A108', 'blocked',  2, NULL,
     '2026-01-15 09:35:00+07', '2026-02-01 08:00:00+07', NULL, NULL, NULL), -- blocked for renovation
    -- Edge case: tombstoned unit (soft-deleted, deleted_at set).
    (gen_random_uuid(), 'U-0009', 'b2000000-0000-0000-0000-000000000001', 'North Tower', '2PN', 'A199', 'available',2,
     '2026-04-01 12:00:00+07', '2026-01-15 09:40:00+07', '2026-04-01 12:00:00+07', NULL, NULL, NULL),

    -- South Tower (A-0002, South Tower / 3PN, project P-0001)
    (gen_random_uuid(), 'U-0010', 'b2000000-0000-0000-0000-000000000002', 'South Tower', '3PN', 'B101', 'available',1, NULL,
     '2026-01-20 09:00:00+07', '2026-01-20 09:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0011', 'b2000000-0000-0000-0000-000000000002', 'South Tower', '3PN', 'B102', 'reserved', 2, NULL,
     '2026-01-20 09:05:00+07', '2026-02-10 09:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0012', 'b2000000-0000-0000-0000-000000000002', 'South Tower', '3PN', 'B103', 'sold',     2, NULL,
     '2026-01-20 09:10:00+07', '2026-02-20 15:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0013', 'b2000000-0000-0000-0000-000000000002', 'South Tower', '3PN', 'B104', 'available',1, NULL,
     '2026-01-20 09:15:00+07', '2026-01-20 09:15:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0014', 'b2000000-0000-0000-0000-000000000002', 'South Tower', '3PN', 'B105', 'available',1, NULL,
     '2026-01-20 09:20:00+07', '2026-01-20 09:20:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0015', 'b2000000-0000-0000-0000-000000000002', 'South Tower', '3PN', 'B106', 'available',1, NULL,
     '2026-01-20 09:25:00+07', '2026-01-20 09:25:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0016', 'b2000000-0000-0000-0000-000000000002', 'South Tower', '3PN', 'B107', 'blocked',  1, NULL,
     '2026-01-20 09:30:00+07', '2026-01-20 09:30:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0017', 'b2000000-0000-0000-0000-000000000002', 'South Tower', '3PN', 'B108', 'available',1, NULL,
     '2026-01-20 09:35:00+07', '2026-01-20 09:35:00+07', NULL, NULL, NULL),

    -- Harbor Block (A-0004, project P-0003 which is archived) — live children.
    (gen_random_uuid(), 'U-0018', 'b2000000-0000-0000-0000-000000000004', 'Harbor Block', '2PN', 'C101', 'sold',     2, NULL,
     '2025-11-05 09:00:00+07', '2025-12-20 15:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0019', 'b2000000-0000-0000-0000-000000000004', 'Harbor Block', '2PN', 'C102', 'available',2, NULL,
     '2025-11-05 09:05:00+07', '2026-01-05 10:00:00+07', NULL, NULL, NULL), -- deal lost, back to available
    (gen_random_uuid(), 'U-0020', 'b2000000-0000-0000-0000-000000000004', 'Harbor Block', '2PN', 'C103', 'available',1, NULL,
     '2025-11-05 09:10:00+07', '2025-11-05 09:10:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0021', 'b2000000-0000-0000-0000-000000000004', 'Harbor Block', '2PN', 'C104', 'available',1, NULL,
     '2025-11-05 09:15:00+07', '2025-11-05 09:15:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0022', 'b2000000-0000-0000-0000-000000000004', 'Harbor Block', '2PN', 'C105', 'available',1, NULL,
     '2025-11-05 09:20:00+07', '2025-11-05 09:20:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'U-0023', 'b2000000-0000-0000-0000-000000000004', 'Harbor Block', '2PN', 'C106', 'available',1, NULL,
     '2025-11-05 09:25:00+07', '2025-11-05 09:25:00+07', NULL, NULL, NULL);

-- =============================================================================
-- Deals
-- =============================================================================

INSERT INTO crm_deals
    (id, external_id, external_unit_id, deal_status, reserved_at, sold_at, lost_at, source_revision,
     deleted_at, created_at, updated_at, mirrored_at, mirrored_revision, last_sync_batch_id)
VALUES
    -- Unit U-0001: multiple deals over time (lead -> reserved [withdrawn] -> sold).
    -- Only one live "holding" (reserved/sold) deal per unit is allowed at a time,
    -- so the withdrawn reservation below is tombstoned (deleted_at set) — this
    -- row doubles as the "tombstoned deal" edge case.
    (gen_random_uuid(), 'D-0001', 'U-0001', 'lead',     NULL,                     NULL,                     NULL, 1, NULL,
     '2026-01-16 10:00:00+07', '2026-01-16 10:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'D-0002', 'U-0001', 'reserved', '2026-02-01 10:00:00+07', NULL,                     NULL, 2,
     '2026-02-20 11:00:00+07', '2026-02-01 10:00:00+07', '2026-02-20 11:00:00+07', NULL, NULL, NULL), -- edge case: tombstoned deal
    (gen_random_uuid(), 'D-0003', 'U-0001', 'sold',     '2026-02-25 09:00:00+07', '2026-03-10 16:00:00+07', NULL, 2, NULL,
     '2026-02-25 09:00:00+07', '2026-03-10 16:00:00+07', NULL, NULL, NULL),

    (gen_random_uuid(), 'D-0004', 'U-0002', 'reserved', '2026-02-05 09:00:00+07', NULL,                     NULL, 1, NULL,
     '2026-02-05 09:00:00+07', '2026-02-05 09:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'D-0005', 'U-0003', 'sold',     '2026-01-25 09:00:00+07', '2026-02-15 14:00:00+07', NULL, 2, NULL,
     '2026-01-25 09:00:00+07', '2026-02-15 14:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'D-0006', 'U-0004', 'lead',     NULL,                     NULL,                     NULL, 1, NULL,
     '2026-01-18 09:00:00+07', '2026-01-18 09:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'D-0007', 'U-0005', 'qualified',NULL,                     NULL,                     NULL, 1, NULL,
     '2026-01-19 09:00:00+07', '2026-01-19 09:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'D-0008', 'U-0006', 'viewing',  NULL,                     NULL,                     NULL, 1, NULL,
     '2026-01-20 09:00:00+07', '2026-01-20 09:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'D-0009', 'U-0010', 'interested',NULL,                    NULL,                     NULL, 1, NULL,
     '2026-01-22 09:00:00+07', '2026-01-22 09:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'D-0010', 'U-0011', 'reserved', '2026-02-10 09:00:00+07', NULL,                     NULL, 1, NULL,
     '2026-02-10 09:00:00+07', '2026-02-10 09:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'D-0011', 'U-0012', 'sold',     '2026-01-28 09:00:00+07', '2026-02-20 15:00:00+07', NULL, 2, NULL,
     '2026-01-28 09:00:00+07', '2026-02-20 15:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'D-0012', 'U-0018', 'sold',     '2025-11-10 09:00:00+07', '2025-12-20 15:00:00+07', NULL, 2, NULL,
     '2025-11-10 09:00:00+07', '2025-12-20 15:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'D-0013', 'U-0019', 'lost',     NULL,                     NULL,                     '2026-01-05 10:00:00+07', 2, NULL,
     '2025-11-12 09:00:00+07', '2026-01-05 10:00:00+07', NULL, NULL, NULL),
    (gen_random_uuid(), 'D-0014', 'U-0013', 'qualified',NULL,                     NULL,                     NULL, 1, NULL,
     '2026-01-25 09:00:00+07', '2026-01-25 09:00:00+07', NULL, NULL, NULL);

COMMIT;
