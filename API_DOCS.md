# FamSilo API Documentation (for Mobile Developers)

The FamSilo API is built using **FastAPI**, which means our backend automatically generates a beautiful, interactive, and self-updating API documentation dashboard using the **Swagger UI** and **OpenAPI** standards.

This guide explains how to access it, how to authenticate, and how to read the schemas.

---

## 1. How to Access the Live API Docs

When the FastAPI server is running on your machine (or staging server), simply navigate to the following URL in your web browser:

👉 **[http://localhost:8000/docs](http://localhost:8000/docs)** 

### What is the Swagger UI?
- It is a fully interactive page where you can see every single available `GET`, `POST`, `PUT`, and `DELETE` endpoint.
- You can expand any endpoint to see the required **JSON Body** or **Query Parameters**.
- You can click **"Try it out"**, fill in the payload, and send an actual request directly from your browser to see the live JSON response.

*(Note: There is also an alternative layout available at [http://localhost:8000/redoc](http://localhost:8000/redoc) if you prefer a more static, reading-focused format).*

---

## 2. Authentication Flow (Supabase)

The FamSilo API requires token-based authentication via our Supabase backend. Mobile developers must implement the Supabase SDK for authentication.

1. **Login:** Use the Supabase auth endpoints (or SDK) to log the user in via email/password.
2. **Access Token:** Upon successful login, Supabase returns a JWT `access_token`.
3. **Authorization Header:** For every subsequent request made to the FamSilo FastAPI backend, you **must** attach this token in the header as a Bearer token.

**Mobile Request Example (Dart/Flutter or Swift/URLSession):**
```http
GET /posts/feed/home HTTP/1.1
Host: api.famsilo.com
Authorization: Bearer <YOUR_SUPABASE_ACCESS_TOKEN>
Content-Type: application/json
```

If the token is missing or expired, the API will return a standard `401 Unauthorized`.

---

## 3. Core Modules Overview

The API is separated into logical routers (tags). Here is a quick cheat sheet of what you will find in the Swagger UI:

### 🛡️ Auth (`/auth`)
- Handled primarily by Supabase on the client-side, but custom token verification endpoints live here.

### 👥 Groups / Silos (`/groups` & `/silos`)
- `POST /groups/create`: Create a new FamSilo group.
- `GET /silos`: Fetch all silos the current user is a member of.
- Manage Group Members constraints (Admins vs. Members).

### 📝 Posts (`/posts`)
- `POST /posts/`: Create a new post. The payload requires a `post_type` (`photo`, `text`, or `proposal`).
- `GET /posts/group/{group_id}`: Fetch the feed for a specific Silo.
- `GET /posts/feed/home`: Fetch the aggregated Dashboard feed of all Silos the user belongs to. 
- *Note:* Our API automatically enriches post feeds with `like_count`, `comment_count`, `upvotes`, and your personal interaction statuses (`liked_by_me`, `my_vote`).

### ❤️ Interactions (`/posts`)
- `POST /posts/{id}/like`: Toggle a like on a post.
- `POST /posts/{id}/comment`: Add a comment to a post.
- `POST /posts/{id}/vote`: Cast an `up` or `down` vote on a Proposal Post. Be aware! Backend automatically marks as "passed" if it crosses the 40% threshold.

### 🔔 Notifications (`/notifications`)
- `GET /notifications`: Get the user's notification bell feed.
- `POST /silo-invites/...`: Send, accept, or decline Silo invitations via the notification system.

---

## 4. Understanding the JSON Schemas

Because we use Pydantic models in Python, the exact shape of the JSON you need to send (and exactly what you will receive) is strictly defined.

At the very bottom of the `http://localhost:8000/docs` page, you will find incredibly detailed **Schemas**. Expanding these schemas will show you data types (`string`, `uuid`, `boolean`, `integer`) and whether a field is optional (`Nullable`) or required.

If you ever receive a `422 Unprocessable Entity` error from the API, it means the JSON payload you sent from the mobile app did not perfectly match the expected Schema document shown in Swagger. Read the error message body—it will specify exactly which field failed validation.
