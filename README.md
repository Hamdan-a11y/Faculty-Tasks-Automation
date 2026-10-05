# Faculty Tasks Automation

Automated faculty assistant and academic workflow platform built with Django, Django REST Framework, and Selenium browser automation.

## Architecture
- **Backend & Web Interface**: `FYP/backend_auth`
  - Integrated Django applications:
    - `auth_app`: Authentication, Google OAuth2, dashboard, portal bridge, attendance exemption.
    - `auto_reminder`: Automated coursework and deadline alerts with Google Classroom & Calendar sync.
    - `class_progress`: Real-time class and lecture progress tracking.
    - `clo_mapper`: Course Learning Outcome mapping and evaluation.
    - `academic_report`: Comprehensive academic report generator and analysis.
    - `final_academic_insights`: Grade and outcome insight visualization.
- **Database**: PostgreSQL 16 (managed via local Docker container or cloud providers like Supabase).

## Database Options

### Option A: Local Docker (PostgreSQL 16)
Start the PostgreSQL container:
```bash
docker compose up -d postgres
```
This starts PostgreSQL 16 on port `5432` with named volume `postgres_data`.
Configure your `.env` in `FYP/backend_auth/.env`:
```env
POSTGRES_PASSWORD=your_password
DATABASE_URL=postgresql://postgres:your_password@localhost:5432/faculty_tasks
```

### Option B: Supabase (Cloud PostgreSQL)
Obtain your Supabase database connection string (Transaction pooler on port `6543` is supported and server-side cursors are disabled automatically):
```env
DATABASE_URL=postgresql://postgres.[project-ref]:[db-password]@aws-0-[region].pooler.supabase.com:6543/postgres
```
`sslmode=require` is automatically enforced for remote hosts.

## Running Locally
1. Navigate to the Django application:
   ```powershell
   cd FYP\backend_auth
   ```
2. Activate your virtual environment:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
3. Run migrations:
   ```powershell
   python manage.py migrate
   ```
4. Start the server:
   ```powershell
   python manage.py runserver 0.0.0.0:8000
   ```
5. Open your browser at:
   ```
   http://127.0.0.1:8000/auth/login/
   ```

## Full Docker Compose Stack
To run both PostgreSQL and Django together in containers:
```bash
docker compose up -d
```
