-- ═══════════════════════════════════════════════════════════════════════════
-- Migration 002: FamSilo AI Agent Suite Tables
-- Run in Supabase SQL Editor
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. Daily Briefings cache ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_briefings (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  briefing_date date NOT NULL DEFAULT CURRENT_DATE,
  summary     text NOT NULL,
  post_count  int DEFAULT 0,
  created_at  timestamptz DEFAULT now(),
  UNIQUE (user_id, briefing_date)
);

-- Only the owner can read their own briefing
ALTER TABLE daily_briefings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "briefings_owner" ON daily_briefings
  FOR ALL USING (auth.uid() = user_id);

-- ── 2. Post Embeddings for RAG ───────────────────────────────────────────────
-- Requires pgvector extension (enable in Supabase: Database → Extensions → vector)
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS post_embeddings (
  post_id         uuid PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
  silo_id         uuid REFERENCES groups(id) ON DELETE CASCADE,
  embedding       vector(768) NOT NULL,
  content_snippet text NOT NULL,
  created_at      timestamptz DEFAULT now()
);

-- IVFFlat index for fast approximate nearest-neighbour cosine search
CREATE INDEX IF NOT EXISTS post_embeddings_vec_idx
  ON post_embeddings USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- RLS: only members of the silo can read embeddings
ALTER TABLE post_embeddings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "embeddings_silo_member" ON post_embeddings
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM group_members gm
      WHERE gm.group_id = post_embeddings.silo_id
        AND gm.user_id = auth.uid()
    )
  );

-- ── 3. AI-generated post flags ───────────────────────────────────────────────
ALTER TABLE posts ADD COLUMN IF NOT EXISTS is_ai_generated boolean DEFAULT false;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS ai_agent text; -- 'facilitator'

-- ── 4. Facilitator run tracking ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS facilitator_runs (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  silo_id     uuid REFERENCES groups(id) ON DELETE CASCADE NOT NULL,
  run_date    date NOT NULL DEFAULT CURRENT_DATE,
  triggered   boolean DEFAULT false,  -- true = AI post was created
  post_id     uuid REFERENCES posts(id) ON DELETE SET NULL,
  created_at  timestamptz DEFAULT now(),
  UNIQUE (silo_id, run_date)
);

-- ── 5. Supabase RPC for cosine similarity search ─────────────────────────────
CREATE OR REPLACE FUNCTION match_silo_posts(
  query_embedding vector(768),
  match_silo_id   uuid,
  match_count     int DEFAULT 5
)
RETURNS TABLE (
  post_id         uuid,
  content_snippet text,
  similarity      float
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    pe.post_id,
    pe.content_snippet,
    1 - (pe.embedding <=> query_embedding) AS similarity
  FROM post_embeddings pe
  WHERE pe.silo_id = match_silo_id
  ORDER BY pe.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
