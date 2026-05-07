<div align="center">
  <img src="https://raw.githubusercontent.com/lucide-icons/lucide/main/icons/server.svg" width="80" height="80" alt="API Logo" />
  <h1 align="center">FamSilo: Backend API & AI Engine</h1>
  <p align="center">
    <strong>A high-performance Python backend powering private family networks with RAG, Vector Search, and Autonomous Agents.</strong>
  </p>
  
  <p align="center">
    <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" /></a>
    <a href="https://supabase.com/"><img src="https://img.shields.io/badge/Supabase-3ECF8E?style=for-the-badge&logo=supabase&logoColor=white" alt="Supabase" /></a>
    <a href="https://deepmind.google/technologies/gemini/"><img src="https://img.shields.io/badge/Gemini_2.5_Flash-1E88E5?style=for-the-badge&logo=google" alt="Gemini" /></a>
    <a href="https://github.com/pgvector/pgvector"><img src="https://img.shields.io/badge/pgvector-336791?style=for-the-badge&logo=postgresql&logoColor=white" alt="pgvector" /></a>
  </p>
</div>

---

## ⚡ Overview

The `Family_Group_API` is the robust neural system behind the FamSilo platform. Built on **FastAPI**, it provides sub-millisecond routing, strict Pydantic data validation, and seamless integration with **Supabase**. 

More than just a CRUD interface, this API houses the **FamSilo AI Agent Suite**—a collection of asynchronous workers and streaming endpoints that bring the family network to life.

---

## 🧠 The AI Architecture

### 1. Vector Database & RAG Pipeline
We leverage **Supabase `pgvector`** to store 768-dimensional text embeddings of family posts. When a user asks the **AI Concierge** a question, the API:
- Embeds the query using `text-embedding-004`.
- Executes a custom PostgreSQL RPC (`match_silo_posts`) to calculate cosine similarity and retrieve the top 5 most relevant historical posts.
- Streams the resulting context-aware answer back to the frontend chunk-by-chunk using **Server-Sent Events (SSE)**.

### 2. Autonomous Agents
- **Silo Facilitator**: Exposed via `/agents/facilitator/check/{silo_id}`. This idempotent endpoint checks the database for a 24-hour dormancy window. If a family chat is quiet, it prompts Gemini to generate a creative engagement post and inserts it natively into the feed.
- **Daily Briefing Cache**: A personalized timeline summarizer that curates unseen activity from the last 24 hours into a warm 2-sentence morning digest, caching it in the `daily_briefings` table to ensure high performance at scale.

### 3. Automated Content Moderation
Every image, video, and text post uploaded to FamSilo passes through an asynchronous Gemini moderation pipeline. 
- Media is temporarily placed in a public bucket.
- A background task downloads the file into memory and streams it to the LLM.
- If flagged, the post is instantly marked `quarantined`, moving the file to a secure bucket to protect the family feed.

---

## 🛠️ Tech Stack & Dependencies

- **Core**: Python 3.10+, FastAPI, Uvicorn
- **Database**: Supabase (PostgreSQL, Row Level Security, pgvector)
- **AI/ML**: `google-genai` SDK (Gemini 2.5 Flash)
- **Validation**: Pydantic v2

---

## 📖 API Documentation

Because this is FastAPI, interactive documentation is generated automatically!

When the server is running, visit:
👉 **[http://localhost:8000/docs](http://localhost:8000/docs)**

*(See [`API_DOCS.md`](./API_DOCS.md) for a detailed walkthrough on authentication, JSON schemas, and available modules for mobile developers).*

---

## 🚀 Local Development

### Prerequisites
- Python 3.10+
- A Supabase Project with the `vector` extension enabled
- Google Gemini API Key

### Setup Instructions

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Environment Configuration**
   Create a `.env` file in the root directory:
   ```env
   SUPABASE_URL=your_supabase_project_url
   SUPABASE_KEY=your_supabase_service_role_key
   GEMINI_API_KEY=your_google_gemini_api_key
   ```

3. **Run Database Migrations**
   Execute the SQL files located in the `/migrations` folder directly inside your Supabase SQL Editor. 
   *Ensure you run `002_ai_agent_tables.sql` to initialize pgvector and the agent tables.*

4. **Start the Server**
   ```bash
   uvicorn main:app --reload
   ```
   The API will be live at `http://localhost:8000`.

---
<div align="center">
  <i>The digital backbone for the modern family.</i>
</div>