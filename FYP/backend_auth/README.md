# Faculty Tasks Automation – Backend & Applications

Session-based Google Sign-In (domain-restricted) for faculty and comprehensive academic task automation. Built with Django + DRF. All UI templates and static assets are served natively via Django apps (`auth_app`, `auto_reminder`, `class_progress`, `clo_mapper`, `final_academic_insights`, `academic_report`).

## Prerequisites
- Python 3.11+
- PostgreSQL 16 reachable (Local Docker container or Supabase instance)
- Google OAuth2 Client (Web application) with redirect URI: `http://localhost:8000/auth/callback/`
- PowerShell (Windows) or Bash (Linux / macOS)

## Quick Start
```powershell
cd FYP\backend_auth
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env   # Fill in SECRET_KEY, DATABASE_URL, and Google OAuth credentials
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

## Database Setup

The backend connects directly to PostgreSQL via `DATABASE_URL` (using `psycopg` binary and `dj-database-url`).

### Local Docker PostgreSQL
Start the PostgreSQL container from the root directory:
```bash
docker compose up -d postgres
```
Set in `.env`:
```env
POSTGRES_PASSWORD=your_password
DATABASE_URL=postgresql://postgres:your_password@localhost:5432/faculty_tasks
```

### Supabase Cloud PostgreSQL
When using Supabase, use the connection pooling URL (port `6543`). The backend automatically configures `sslmode=require` and disables server-side cursors for the transaction pooler:
```env
DATABASE_URL=postgresql://postgres.[project-ref]:[db-password]@aws-0-[region].pooler.supabase.com:6543/postgres
```

## CLO Mapper & OCR Setup (Optional)
- Tesseract OCR:
  - Ubuntu/Debian: `sudo apt-get install tesseract-ocr`
  - Windows/macOS: Installers from https://tesseract-ocr.github.io/tessdoc/Installation.html
- NLTK Tokenizer / Stopwords:
  ```bash
  python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords')"
  ```

## Environment Configuration (.env)
```env
SECRET_KEY=your-django-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
FRONTEND_ORIGIN=http://localhost:5500

POSTGRES_PASSWORD=your-postgres-password
DATABASE_URL=postgresql://postgres:your-postgres-password@localhost:5432/faculty_tasks

GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback/
```

## Key Endpoints
- `GET /` → redirects to `/auth/login/`.
- `GET /auth/login/` → renders login page.
- `GET /auth/google/` → redirects to Google OAuth consent.
- `GET /auth/callback/` → handles OAuth code, validates `@szabist-isb.pk` domain, creates session, redirects to dashboard.
- `GET /auth/dashboard/` → renders dashboard (requires session or redirects to login).
- `GET /auth/user/` → returns current session user JSON (401 if unauthenticated).
- `GET /auth/logout/` → clears session, redirects to login.
- `GET /auth/unauthorized/` → renders unauthorized access page.

## Notes
- Sessions are stored server-side.
- All UI is served via Django templates and static files.
- Domain restriction: Only email addresses ending with `@szabist-isb.pk` are authorized.
- Database: Exclusively powered by PostgreSQL via `DATABASE_URL`. SQLite and MySQL support have been fully removed.
