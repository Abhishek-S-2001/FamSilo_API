-- ============================================================
-- Migration: Zero-Trust AI Content Moderation Audit Log
-- Run this in the Supabase SQL Editor (Dashboard → SQL Editor)
-- ============================================================

-- 1. Create the moderation_logs table
CREATE TABLE IF NOT EXISTS public.moderation_logs (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id       uuid        REFERENCES public.posts(id) ON DELETE CASCADE,
    content_type  text        NOT NULL CHECK (content_type IN ('text', 'image', 'video', 'comment')),
    verdict       text        NOT NULL CHECK (verdict IN ('approved', 'quarantined')),
    flags         text[]      NOT NULL DEFAULT '{}',
    reason        text,
    reviewed_by   text        NOT NULL DEFAULT 'ai',  -- 'ai' or admin user_id UUID
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- 2. Index for fast lookup by post_id (used by /moderation/status/:post_id)
CREATE INDEX IF NOT EXISTS idx_moderation_logs_post_id
    ON public.moderation_logs (post_id, created_at DESC);

-- 3. Enable Row Level Security
ALTER TABLE public.moderation_logs ENABLE ROW LEVEL SECURITY;

-- 4. RLS Policy: Service-role key (backend) can read/write all rows
--    (The FastAPI backend uses the service_role key, so this is covered.)
--    No policy needed for the anon/user role — the frontend never accesses this table directly.

-- 5. (Optional) Grant select to authenticated role if you ever want to
--    expose read-only audit data to logged-in users via the JS client:
-- GRANT SELECT ON public.moderation_logs TO authenticated;

-- 6. Confirm the table was created
SELECT 'moderation_logs table created successfully' AS status;
