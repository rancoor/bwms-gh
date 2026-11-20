-- Supabase-compatible, dependency-ordered schema for the supplied production schema.
-- Generated from the supplied schema-only export.
--
-- Run this against a fresh Supabase project (SQL Editor or psql).
-- The Supabase-managed auth.users table is intentionally NOT created here.
-- user_profiles.id references auth.users(id), so matching Auth users must exist
-- before importing production.user_profiles rows.
--
-- This file creates the production schema and the 25 supplied tables in an
-- order that satisfies their foreign-key dependencies.

CREATE SCHEMA IF NOT EXISTS production;

CREATE TABLE production.cell_categories (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  code text NOT NULL UNIQUE,
  name text NOT NULL,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT cell_categories_pkey PRIMARY KEY (id)
);

CREATE TABLE production.grn_number_counters (
  year integer NOT NULL,
  last_value integer NOT NULL DEFAULT 0,
  CONSTRAINT grn_number_counters_pkey PRIMARY KEY (year)
);

CREATE TABLE production.items (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  item_code text NOT NULL UNIQUE,
  item_type_rule text NOT NULL CHECK (item_type_rule = ANY (ARRAY['OSB'::text, 'OTHER'::text])),
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  pieces_per_stack integer,
  supplier_description text,
  d365_description text,
  CONSTRAINT items_pkey PRIMARY KEY (id)
);

CREATE TABLE production.removal_number_counters (
  year integer NOT NULL,
  last_value integer NOT NULL DEFAULT 0,
  CONSTRAINT removal_number_counters_pkey PRIMARY KEY (year)
);

CREATE TABLE production.roles (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  name text NOT NULL UNIQUE,
  description text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  is_system boolean NOT NULL DEFAULT false,
  is_superuser boolean NOT NULL DEFAULT false,
  CONSTRAINT roles_pkey PRIMARY KEY (id)
);

CREATE TABLE production.stack_code_counters (
  year integer NOT NULL,
  last_value integer NOT NULL DEFAULT 0,
  CONSTRAINT stack_code_counters_pkey PRIMARY KEY (year)
);

CREATE TABLE production.cell_capacity_rules (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  category_id uuid NOT NULL,
  item_type_rule text NOT NULL CHECK (item_type_rule = ANY (ARRAY['OSB'::text, 'OTHER'::text])),
  max_stacks integer NOT NULL,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT cell_capacity_rules_pkey PRIMARY KEY (id),
  CONSTRAINT cell_capacity_rules_category_id_fkey FOREIGN KEY (category_id) REFERENCES production.cell_categories(id)
);

CREATE TABLE production.cells (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  cell_code text NOT NULL UNIQUE,
  location_code text NOT NULL DEFAULT 'NBW-BOND'::text,
  zone text NOT NULL,
  position_type text NOT NULL DEFAULT 'OTHER'::text CHECK (position_type = ANY (ARRAY['STORAGE'::text, 'STAGING'::text, 'UNUSABLE'::text])),
  max_stacks integer NOT NULL,
  current_stack_count integer NOT NULL DEFAULT 0,
  grid_row integer,
  grid_col numeric,
  category_id uuid,
  CONSTRAINT cells_pkey PRIMARY KEY (id),
  CONSTRAINT cells_category_id_fkey FOREIGN KEY (category_id) REFERENCES production.cell_categories(id)
);

CREATE TABLE production.role_capabilities (
  role_id uuid NOT NULL,
  capability text NOT NULL,
  CONSTRAINT role_capabilities_pkey PRIMARY KEY (role_id, capability),
  CONSTRAINT custom_role_capabilities_custom_role_id_fkey FOREIGN KEY (role_id) REFERENCES production.roles(id)
);

CREATE TABLE production.user_profiles (
  id uuid NOT NULL,
  full_name text NOT NULL,
  is_active boolean NOT NULL DEFAULT true,
  role_id uuid NOT NULL,
  CONSTRAINT user_profiles_pkey PRIMARY KEY (id),
  CONSTRAINT user_profiles_id_fkey FOREIGN KEY (id) REFERENCES auth.users(id),
  CONSTRAINT user_profiles_custom_role_id_fkey FOREIGN KEY (role_id) REFERENCES production.roles(id)
);

CREATE TABLE production.audit_log (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  event_type text NOT NULL,
  user_id uuid,
  reference_id uuid,
  payload jsonb,
  ip_address inet,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT audit_log_pkey PRIMARY KEY (id),
  CONSTRAINT audit_log_user_id_fkey FOREIGN KEY (user_id) REFERENCES production.user_profiles(id)
);

CREATE TABLE production.removal_requests (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  removal_type text NOT NULL CHECK (removal_type = ANY (ARRAY['C410'::text, 'C400'::text])),
  raised_by uuid NOT NULL,
  approved_by uuid,
  status text NOT NULL DEFAULT 'DRAFT'::text CHECK (status = ANY (ARRAY['DRAFT'::text, 'PENDING_REVIEW'::text, 'APPROVED'::text, 'REJECTED'::text, 'EMAIL_SENT'::text, 'DOCS_UPLOADED'::text, 'RO_VERIFIED'::text, 'SCAN_IN_PROGRESS'::text, 'COMPLETED'::text])),
  kra_email_sent_at timestamp with time zone,
  pick_note_printed_at timestamp with time zone,
  completed_at timestamp with time zone,
  rejection_reason text,
  removal_number text,
  export_journal_reference text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  fefo_override boolean NOT NULL DEFAULT false,
  fefo_override_reason text,
  CONSTRAINT removal_requests_pkey PRIMARY KEY (id),
  CONSTRAINT removal_requests_raised_by_fkey FOREIGN KEY (raised_by) REFERENCES production.user_profiles(id),
  CONSTRAINT removal_requests_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES production.user_profiles(id)
);

CREATE TABLE production.system_config (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  key text NOT NULL UNIQUE,
  value text,
  updated_by uuid,
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT system_config_pkey PRIMARY KEY (id),
  CONSTRAINT system_config_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES production.user_profiles(id)
);

CREATE TABLE production.w700_entries (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  sad_reference text NOT NULL UNIQUE,
  declaration_type text NOT NULL,
  procedure_code text NOT NULL,
  warehouse_id text NOT NULL,
  bond_licence_ref text,
  stamp_date_time timestamp with time zone NOT NULL,
  expiry_date date NOT NULL,
  status text NOT NULL DEFAULT 'ACTIVE'::text CHECK (status = ANY (ARRAY['ACTIVE'::text, 'EXPIRED'::text, 'EXHAUSTED'::text])),
  cif_value_kes numeric NOT NULL,
  raw_pdf_path text,
  icms_confirmed_by uuid,
  icms_confirmed_at timestamp with time zone,
  importer_pin text,
  total_freight_kes numeric,
  total_insurance_kes numeric,
  total_other_charges_kes numeric,
  total_customs_value_kes numeric,
  exit_arrival_date timestamp with time zone,
  exchange_rate numeric,
  bill_of_lading text,
  raw_document_path text,
  po_number text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT w700_entries_pkey PRIMARY KEY (id),
  CONSTRAINT w700_entries_icms_confirmed_by_fkey FOREIGN KEY (icms_confirmed_by) REFERENCES production.user_profiles(id)
);

CREATE TABLE production.export_documents (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  removal_request_id uuid NOT NULL,
  doc_type text NOT NULL,
  file_path text NOT NULL,
  captured_at timestamp with time zone NOT NULL DEFAULT now(),
  reference_number text,
  CONSTRAINT export_documents_pkey PRIMARY KEY (id),
  CONSTRAINT export_documents_removal_request_id_fkey FOREIGN KEY (removal_request_id) REFERENCES production.removal_requests(id)
);

CREATE TABLE production.bond_movements (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  bond_type text NOT NULL CHECK (bond_type = ANY (ARRAY['CB3'::text, 'CB6'::text])),
  movement_type text NOT NULL CHECK (movement_type = ANY (ARRAY['INCREASE'::text, 'DECREASE'::text])),
  amount_kes numeric NOT NULL,
  trigger_event text NOT NULL,
  reference_id uuid,
  w700_entry_id uuid,
  balance_after_kes numeric NOT NULL,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT bond_movements_pkey PRIMARY KEY (id),
  CONSTRAINT bond_movements_w700_entry_id_fkey FOREIGN KEY (w700_entry_id) REFERENCES production.w700_entries(id)
);

CREATE TABLE production.removal_w700_groups (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  removal_request_id uuid NOT NULL,
  w700_entry_id uuid NOT NULL,
  ro_number text,
  ro_document_path text,
  ro_verified_at timestamp with time zone,
  ibt_reference text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT removal_w700_groups_pkey PRIMARY KEY (id),
  CONSTRAINT removal_w700_groups_w700_entry_id_fkey FOREIGN KEY (w700_entry_id) REFERENCES production.w700_entries(id),
  CONSTRAINT removal_w700_groups_removal_request_id_fkey FOREIGN KEY (removal_request_id) REFERENCES production.removal_requests(id)
);

CREATE TABLE production.w700_containers (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  w700_entry_id uuid NOT NULL,
  container_number text NOT NULL,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT w700_containers_pkey PRIMARY KEY (id),
  CONSTRAINT w700_containers_w700_entry_id_fkey FOREIGN KEY (w700_entry_id) REFERENCES production.w700_entries(id)
);

CREATE TABLE production.goods_in_sessions (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  w700_entry_id uuid NOT NULL,
  po_number text,
  ghala_grn text,
  d365_grn text,
  logistics_docs_confirmed boolean NOT NULL DEFAULT false,
  seal_intact_photo_path text,
  seal_broken_photo_path text,
  seal_broken_at timestamp with time zone,
  seal_broken_by uuid,
  icms_status text,
  scan_in_complete boolean NOT NULL DEFAULT false,
  notified_at timestamp with time zone,
  status text NOT NULL DEFAULT 'DRAFT'::text,
  grn_request_sent_at timestamp with time zone,
  grn_request_sent_by uuid,
  seal_number text,
  w700_container_id uuid,
  CONSTRAINT goods_in_sessions_pkey PRIMARY KEY (id),
  CONSTRAINT goods_in_sessions_w700_entry_id_fkey FOREIGN KEY (w700_entry_id) REFERENCES production.w700_entries(id),
  CONSTRAINT goods_in_sessions_seal_broken_by_fkey FOREIGN KEY (seal_broken_by) REFERENCES production.user_profiles(id),
  CONSTRAINT goods_in_sessions_grn_request_sent_by_fkey FOREIGN KEY (grn_request_sent_by) REFERENCES production.user_profiles(id),
  CONSTRAINT goods_in_sessions_w700_container_id_fkey FOREIGN KEY (w700_container_id) REFERENCES production.w700_containers(id)
);

CREATE TABLE production.w700_lines (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  w700_entry_id uuid NOT NULL,
  item_number text NOT NULL,
  hs_code text NOT NULL,
  goods_description text NOT NULL,
  authorised_qty numeric NOT NULL,
  qty_uom text NOT NULL,
  qty_received numeric NOT NULL DEFAULT 0,
  qty_remaining numeric NOT NULL,
  cif_value_kes numeric NOT NULL,
  bill_code text,
  import_duty_kes numeric,
  excise_duty_kes numeric,
  vat_imports_kes numeric,
  mss_levy_kes numeric,
  qty_assigned numeric NOT NULL DEFAULT 0,
  idf_fees_kes numeric,
  rdl_kes numeric,
  w700_container_id uuid,
  CONSTRAINT w700_lines_pkey PRIMARY KEY (id),
  CONSTRAINT w700_lines_w700_entry_id_fkey FOREIGN KEY (w700_entry_id) REFERENCES production.w700_entries(id),
  CONSTRAINT w700_lines_w700_container_id_fkey FOREIGN KEY (w700_container_id) REFERENCES production.w700_containers(id)
);

CREATE TABLE production.goods_in_po_lines (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  goods_in_session_id uuid NOT NULL,
  item_number text,
  description text,
  ordered_quantity numeric NOT NULL,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  w700_entry_id uuid,
  CONSTRAINT goods_in_po_lines_pkey PRIMARY KEY (id),
  CONSTRAINT goods_in_po_lines_goods_in_session_id_fkey FOREIGN KEY (goods_in_session_id) REFERENCES production.goods_in_sessions(id),
  CONSTRAINT goods_in_po_lines_w700_entry_id_fkey FOREIGN KEY (w700_entry_id) REFERENCES production.w700_entries(id)
);

CREATE TABLE production.goods_in_issues (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  w700_line_id uuid NOT NULL,
  goods_in_session_id uuid NOT NULL,
  issue_type text NOT NULL CHECK (issue_type = ANY (ARRAY['SHORT_SUPPLY'::text, 'OVER_SUPPLY'::text, 'WRONG_ITEM'::text, 'WRONG_COLOR'::text, 'DAMAGED'::text, 'OTHER'::text])),
  description text NOT NULL,
  actual_item_code text,
  actual_qty_pcs numeric,
  reported_by uuid NOT NULL,
  resolved_at timestamp with time zone,
  resolved_by uuid,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT goods_in_issues_pkey PRIMARY KEY (id),
  CONSTRAINT goods_in_issues_w700_line_id_fkey FOREIGN KEY (w700_line_id) REFERENCES production.w700_lines(id),
  CONSTRAINT goods_in_issues_goods_in_session_id_fkey FOREIGN KEY (goods_in_session_id) REFERENCES production.goods_in_sessions(id),
  CONSTRAINT goods_in_issues_reported_by_fkey FOREIGN KEY (reported_by) REFERENCES production.user_profiles(id),
  CONSTRAINT goods_in_issues_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES production.user_profiles(id)
);

CREATE TABLE production.stacks (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  stack_code text NOT NULL UNIQUE,
  w700_line_id uuid NOT NULL,
  cell_id uuid NOT NULL,
  item_code text NOT NULL,
  qty_pcs integer NOT NULL,
  gross_mass_kg numeric,
  entry_date date NOT NULL,
  expiry_date date NOT NULL,
  status text NOT NULL DEFAULT 'IN_BOND'::text CHECK (status = ANY (ARRAY['IN_BOND'::text, 'IN_TRANSIT'::text, 'RELEASED'::text, 'EXPORTED'::text])),
  scanned_at timestamp with time zone,
  d365_description text,
  goods_in_session_id uuid,
  CONSTRAINT stacks_pkey PRIMARY KEY (id),
  CONSTRAINT stacks_w700_line_id_fkey FOREIGN KEY (w700_line_id) REFERENCES production.w700_lines(id),
  CONSTRAINT stacks_cell_id_fkey FOREIGN KEY (cell_id) REFERENCES production.cells(id),
  CONSTRAINT stacks_goods_in_session_id_fkey FOREIGN KEY (goods_in_session_id) REFERENCES production.goods_in_sessions(id)
);

CREATE TABLE production.removal_lines (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  removal_request_id uuid NOT NULL,
  stack_id uuid NOT NULL,
  w700_line_id uuid NOT NULL,
  qty_to_remove numeric NOT NULL,
  scanned_out_at timestamp with time zone,
  fefo_override boolean NOT NULL DEFAULT false,
  CONSTRAINT removal_lines_pkey PRIMARY KEY (id),
  CONSTRAINT removal_lines_removal_request_id_fkey FOREIGN KEY (removal_request_id) REFERENCES production.removal_requests(id),
  CONSTRAINT removal_lines_stack_id_fkey FOREIGN KEY (stack_id) REFERENCES production.stacks(id),
  CONSTRAINT removal_lines_w700_line_id_fkey FOREIGN KEY (w700_line_id) REFERENCES production.w700_lines(id)
);

CREATE TABLE production.stack_movements (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  stack_id uuid NOT NULL,
  from_cell_id uuid NOT NULL,
  to_cell_id uuid NOT NULL,
  moved_by uuid,
  reason text,
  moved_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT stack_movements_pkey PRIMARY KEY (id),
  CONSTRAINT stack_movements_stack_id_fkey FOREIGN KEY (stack_id) REFERENCES production.stacks(id),
  CONSTRAINT stack_movements_from_cell_id_fkey FOREIGN KEY (from_cell_id) REFERENCES production.cells(id),
  CONSTRAINT stack_movements_to_cell_id_fkey FOREIGN KEY (to_cell_id) REFERENCES production.cells(id),
  CONSTRAINT stack_movements_moved_by_fkey FOREIGN KEY (moved_by) REFERENCES production.user_profiles(id)
);

-- IMPORTANT DATA IMPORT NOTES:
-- 1. Import parent tables before child tables (the order above is dependency-safe).
-- 2. production.user_profiles.id must match an existing auth.users.id.
--    CSV import cannot create Supabase Auth users by itself.
-- 3. This export contains table definitions and inline constraints only.
--    It does not include RLS policies, grants, storage buckets, Auth configuration,
--    Edge Functions, or application secrets.
