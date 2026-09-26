
from fastapi import (
    FastAPI, UploadFile, File, HTTPException, Depends
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, EmailStr
from pwdlib import PasswordHash
from pathlib import Path
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3
import uuid
import jwt
import os

# ==========================================================
# KHAZANA - Distributed Object Storage (Hackathon MVP)
# ==========================================================
# Features:
# - Authentication and user accounts
# - File upload, list, download
# - SHA256 integrity verification
# - 3 logical storage replicas
# - Node failure simulation
# - Replica repair
#
# IMPORTANT:
# These nodes are directories on the same backend filesystem.
# This is a demo, not real multi-server fault tolerance.
# Render Free filesystem may be ephemeral.
# ==========================================================

app = FastAPI(title="Khazana Storage API", version="2.0.0")

FRONTEND_ORIGIN = os.getenv(
    "FRONTEND_ORIGIN",
    "https://khazanaa-mkpp3jure-vijay-laxmi-2582006.vercel.app"
).rstrip("/")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REPLICA_DIR = DATA_DIR / "replicas"

DATA_DIR.mkdir(parents=True, exist_ok=True)
REPLICA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE_DIR / "vault.db"

SECRET_KEY = os.getenv(
    "KHAZANA_SECRET_KEY",
    "dev-only-change-this-secret-before-deployment"
)
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 1440

REPLICATION_FACTOR = 3
NODE_IDS = ["node-1", "node-2", "node-3"]

password_hash = PasswordHash.recommended()
security = HTTPBearer()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def db():
    return sqlite3.connect(DB_PATH, timeout=30)


# ==========================================================
# DATABASE
# ==========================================================

def init_db():
    with db() as conn:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                storage_used INTEGER NOT NULL DEFAULT 0,
                storage_limit INTEGER NOT NULL DEFAULT 104857600
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                size INTEGER NOT NULL,
                checksum TEXT NOT NULL,
                stored_path TEXT NOT NULL
            )
        """)

        file_columns = [
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(files)"
            ).fetchall()
        ]

        if "owner_id" not in file_columns:
            conn.execute("""
                ALTER TABLE files
                ADD COLUMN owner_id TEXT REFERENCES users(id)
            """)

        user_columns = [
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(users)"
            ).fetchall()
        ]

        if "storage_used" not in user_columns:
            conn.execute("""
                ALTER TABLE users
                ADD COLUMN storage_used INTEGER NOT NULL DEFAULT 0
            """)

        if "storage_limit" not in user_columns:
            conn.execute("""
                ALTER TABLE users
                ADD COLUMN storage_limit INTEGER NOT NULL DEFAULT 104857600
            """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS storage_nodes (
                node_id TEXT PRIMARY KEY,
                is_active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS replicas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                replica_path TEXT NOT NULL,
                checksum TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'healthy',
                updated_at TEXT NOT NULL,
                UNIQUE(file_id, node_id)
            )
        """)

        for node_id in NODE_IDS:
            conn.execute("""
                INSERT OR IGNORE INTO storage_nodes
                (node_id, is_active, updated_at)
                VALUES (?, 1, ?)
            """, (node_id, utc_now()))

        conn.commit()


init_db()


# ==========================================================
# REQUEST MODELS
# ==========================================================

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class NodeStatusRequest(BaseModel):
    active: bool


# ==========================================================
# AUTHENTICATION
# ==========================================================

def create_access_token(user_id: str):
    expiry = datetime.now(timezone.utc) + timedelta(
        minutes=TOKEN_EXPIRE_MINUTES
    )

    return jwt.encode(
        {"sub": user_id, "exp": expiry},
        SECRET_KEY,
        algorithm=ALGORITHM
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    try:
        payload = jwt.decode(
            credentials.credentials,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        user_id = payload.get("sub")

        if not user_id:
            raise HTTPException(
                status_code=401,
                detail="Invalid token"
            )

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Session expired. Please login again."
        )

    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token"
        )

    with db() as conn:
        conn.row_factory = sqlite3.Row

        user = conn.execute("""
            SELECT id, name, email, created_at
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()

    if not user:
        raise HTTPException(
            status_code=401,
            detail="User not found"
        )

    return dict(user)


# ==========================================================
# REPLICATION HELPERS
# ==========================================================

def get_active_nodes():
    with db() as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT node_id
            FROM storage_nodes
            WHERE is_active = 1
            ORDER BY node_id
        """).fetchall()

    return [row["node_id"] for row in rows]


def replica_path(node_id: str, file_id: str):
    # Node IDs and file IDs are generated by this application.
    return REPLICA_DIR / node_id / file_id


def save_replica(file_id, node_id, content, checksum):
    destination = replica_path(node_id, file_id)
    destination.parent.mkdir(parents=True, exist_ok=True)

    temp_path = destination.with_suffix(".tmp")
    temp_path.write_bytes(content)
    temp_path.replace(destination)

    with db() as conn:
        conn.execute("""
            INSERT INTO replicas
            (file_id, node_id, replica_path, checksum,
             status, updated_at)
            VALUES (?, ?, ?, ?, 'healthy', ?)

            ON CONFLICT(file_id, node_id)
            DO UPDATE SET
                replica_path = excluded.replica_path,
                checksum = excluded.checksum,
                status = 'healthy',
                updated_at = excluded.updated_at
        """, (
            file_id,
            node_id,
            str(destination),
            checksum,
            utc_now()
        ))

        conn.commit()


def get_replica_records(file_id):
    with db() as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT r.node_id, r.replica_path, r.checksum,
                   r.status, n.is_active
            FROM replicas r
            JOIN storage_nodes n
              ON n.node_id = r.node_id
            WHERE r.file_id = ?
            ORDER BY r.node_id
        """, (file_id,)).fetchall()

    return [dict(row) for row in rows]


def get_valid_copy(file_id, expected_checksum):
    """
    Read from an active replica only if its SHA256 checksum
    matches the checksum stored in metadata.
    """

    records = get_replica_records(file_id)

    for replica in records:
        if not replica["is_active"]:
            continue

        path = Path(replica["replica_path"])

        if not path.is_file():
            continue

        try:
            content = path.read_bytes()

            actual_checksum = hashlib.sha256(
                content
            ).hexdigest()

            if actual_checksum == expected_checksum:
                return content

        except OSError:
            continue

    return None


def repair_file(file_id):
    """
    Recover a valid copy and recreate missing or corrupt
    replicas on active logical nodes.
    """

    with db() as conn:
        conn.row_factory = sqlite3.Row

        file_row = conn.execute("""
            SELECT checksum, stored_path
            FROM files
            WHERE id = ?
        """, (file_id,)).fetchone()

    if not file_row:
        return {
            "file_id": file_id,
            "repaired": 0,
            "message": "File metadata not found"
        }

    expected_checksum = file_row["checksum"]

    # First, recover from a healthy active replica.
    content = get_valid_copy(
        file_id,
        expected_checksum
    )

    # Fallback for older uploads made before replication.
    if content is None:
        legacy_path = Path(file_row["stored_path"])

        if legacy_path.is_file():
            candidate = legacy_path.read_bytes()

            if hashlib.sha256(candidate).hexdigest() == expected_checksum:
                content = candidate

    if content is None:
        return {
            "file_id": file_id,
            "repaired": 0,
            "message": "No valid copy found; recovery is not possible"
        }

    repaired = 0

    for node_id in get_active_nodes():

        path = replica_path(node_id, file_id)
        is_valid = False

        if path.is_file():
            try:
                existing = path.read_bytes()

                is_valid = (
                    hashlib.sha256(existing).hexdigest()
                    == expected_checksum
                )
            except OSError:
                is_valid = False

        if not is_valid:
            save_replica(
                file_id,
                node_id,
                content,
                expected_checksum
            )
            repaired += 1

    return {
        "file_id": file_id,
        "repaired": repaired,
        "replication_factor": REPLICATION_FACTOR,
        "active_nodes": get_active_nodes(),
        "message": "Repair check completed"
    }


def replica_status(file_id):
    records = get_replica_records(file_id)
    output = []

    for replica in records:
        path = Path(replica["replica_path"])
        exists = path.is_file()

        status = replica["status"]

        if not exists:
            status = "missing"
        elif not replica["is_active"]:
            status = "offline"

        output.append({
            "node_id": replica["node_id"],
            "status": status,
            "exists": exists,
            "active": bool(replica["is_active"])
        })

    return output


# ==========================================================
# BASIC ROUTES
# ==========================================================

@app.get("/")
def home():
    return {
        "message": "Khazana API is running",
        "version": "2.0.0"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "Khazana"
    }


# ==========================================================
# REGISTER
# ==========================================================

@app.post("/api/register")
def register(data: RegisterRequest):

    name = data.name.strip()
    email = data.email.lower().strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Name is required"
        )

    if len(data.password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters"
        )

    user_id = str(uuid.uuid4())
    hashed_password = password_hash.hash(data.password)

    try:
        with db() as conn:
            conn.execute("""
                INSERT INTO users
                (id, name, email, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                user_id,
                name,
                email,
                hashed_password,
                utc_now()
            ))

            conn.commit()

    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Email is already registered"
        )

    return {
        "message": "Account created successfully",
        "access_token": create_access_token(user_id),
        "token_type": "bearer",
        "user": {
            "id": user_id,
            "name": name,
            "email": email
        }
    }


# ==========================================================
# LOGIN
# ==========================================================

@app.post("/api/login")
def login(data: LoginRequest):

    email = data.email.lower().strip()

    with db() as conn:
        conn.row_factory = sqlite3.Row

        user = conn.execute("""
            SELECT id, name, email, password_hash
            FROM users
            WHERE email = ?
        """, (email,)).fetchone()

    if not user or not password_hash.verify(
        data.password,
        user["password_hash"]
    ):
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password"
        )

    return {
        "message": "Login successful",
        "access_token": create_access_token(user["id"]),
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"]
        }
    }


# ==========================================================
# CURRENT USER
# ==========================================================

@app.get("/api/me")
def get_me(user=Depends(get_current_user)):
    return user


# ==========================================================
# UPLOAD WITH REPLICATION
# ==========================================================

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    user=Depends(get_current_user)
):

    content = await file.read()

    if not content:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty"
        )

    file_id = str(uuid.uuid4())
    filename = file.filename or "unnamed"
    checksum = hashlib.sha256(content).hexdigest()

    # Check user storage quota.
    with db() as conn:
        conn.row_factory = sqlite3.Row

        user_row = conn.execute("""
            SELECT storage_used, storage_limit
            FROM users
            WHERE id = ?
        """, (user["id"],)).fetchone()

    if user_row and (
        user_row["storage_used"] + len(content)
        > user_row["storage_limit"]
    ):
        raise HTTPException(
            status_code=413,
            detail="Storage limit exceeded"
        )

    active_nodes = get_active_nodes()

    if not active_nodes:
        raise HTTPException(
            status_code=503,
            detail="No active storage nodes available"
        )

    # Keep the original location for compatibility.
    user_dir = DATA_DIR / user["id"]
    user_dir.mkdir(parents=True, exist_ok=True)

    original_path = user_dir / file_id

    try:
        original_path.write_bytes(content)

        # Replicate the content to active logical nodes.
        replicated_nodes = []

        for node_id in active_nodes:
            save_replica(
                file_id,
                node_id,
                content,
                checksum
            )
            replicated_nodes.append(node_id)

        with db() as conn:

            conn.execute("""
                INSERT INTO files
                (id, filename, size, checksum,
                 stored_path, owner_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                file_id,
                filename,
                len(content),
                checksum,
                str(original_path),
                user["id"]
            ))

            conn.execute("""
                UPDATE users
                SET storage_used = storage_used + ?
                WHERE id = ?
            """, (len(content), user["id"]))

            conn.commit()

        return {
            "message": "File uploaded successfully",
            "id": file_id,
            "filename": filename,
            "size": len(content),
            "checksum": checksum,
            "status": "stored",
            "replication_factor": REPLICATION_FACTOR,
            "replicas_created": len(replicated_nodes),
            "replica_nodes": replicated_nodes
        }

    except Exception as exc:
        original_path.unlink(missing_ok=True)

        raise HTTPException(
            status_code=500,
            detail=f"Upload failed: {str(exc)}"
        )


# ==========================================================
# LIST USER FILES
# ==========================================================

@app.get("/api/files")
def list_files(user=Depends(get_current_user)):

    with db() as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT id, filename, size, checksum
            FROM files
            WHERE owner_id = ?
            ORDER BY rowid DESC
        """, (user["id"],)).fetchall()

    output = []

    for row in rows:
        item = dict(row)

        replicas = replica_status(row["id"])

        item["replicas"] = replicas

        item["replica_count"] = sum(
            1 for replica in replicas
            if replica["exists"]
            and replica["status"] == "healthy"
            and replica["active"]
        )

        item["replication_factor"] = REPLICATION_FACTOR

        output.append(item)

    return output


# ==========================================================
# OPEN / DOWNLOAD FILE
# ==========================================================

@app.get("/api/files/{file_id}/open")
def open_file(
    file_id: str,
    user=Depends(get_current_user)
):

    with db() as conn:
        conn.row_factory = sqlite3.Row

        row = conn.execute("""
            SELECT id, filename, stored_path, checksum
            FROM files
            WHERE id = ? AND owner_id = ?
        """, (
            file_id,
            user["id"]
        )).fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="File not found"
        )

    # Read from a healthy active replica first.
    content = get_valid_copy(
        file_id,
        row["checksum"]
    )

    if content is not None:
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition":
                f'inline; filename="{row["filename"]}"'
            }
        )

    # Fallback to the original stored file.
    legacy_path = Path(row["stored_path"]).resolve()

    user_dir = (
        DATA_DIR / user["id"]
    ).resolve()

    if not legacy_path.is_relative_to(user_dir):
        raise HTTPException(
            status_code=403,
            detail="Invalid file location"
        )

    if legacy_path.is_file():
        legacy_content = legacy_path.read_bytes()

        if hashlib.sha256(
            legacy_content
        ).hexdigest() == row["checksum"]:

            return FileResponse(
                path=str(legacy_path),
                filename=row["filename"],
                content_disposition_type="inline"
            )

    raise HTTPException(
        status_code=503,
        detail="No healthy copy found. Run replica repair."
    )


# ==========================================================
# STORAGE HEALTH / DASHBOARD
# ==========================================================

@app.get("/api/storage/health")
def storage_health(user=Depends(get_current_user)):

    with db() as conn:
        conn.row_factory = sqlite3.Row

        nodes = conn.execute("""
            SELECT node_id, is_active, updated_at
            FROM storage_nodes
            ORDER BY node_id
        """).fetchall()

        files = conn.execute("""
            SELECT id, size
            FROM files
            WHERE owner_id = ?
        """, (user["id"],)).fetchall()

    node_data = []

    for node in nodes:
        node_id = node["node_id"]

        with db() as conn:
            count = conn.execute("""
                SELECT COUNT(*)
                FROM replicas
                WHERE node_id = ?
            """, (node_id,)).fetchone()[0]

        node_data.append({
            "node_id": node_id,
            "status": (
                "healthy" if node["is_active"]
                else "offline"
            ),
            "active": bool(node["is_active"]),
            "replica_count": count,
            "updated_at": node["updated_at"]
        })

    total_files = len(files)
    total_bytes = sum(f["size"] for f in files)

    total_replicas = 0

    for f in files:
        statuses = replica_status(f["id"])

        total_replicas += sum(
            1 for replica in statuses
            if replica["exists"]
            and replica["status"] == "healthy"
            and replica["active"]
        )

    desired_replicas = total_files * REPLICATION_FACTOR

    health_percent = (
        100
        if desired_replicas == 0
        else round(
            total_replicas / desired_replicas * 100,
            2
        )
    )

    return {
        "service": "Khazana",
        "mode": "logical_replication_demo",
        "replication_factor": REPLICATION_FACTOR,
        "total_files": total_files,
        "total_original_bytes": total_bytes,
        "total_healthy_replicas": total_replicas,
        "desired_replicas": desired_replicas,
        "replication_health_percent": health_percent,
        "nodes": node_data,
        "note": (
            "Logical replicas use the same backend filesystem. "
            "They are not independent physical servers."
        )
    }


@app.get("/api/storage/nodes")
def list_nodes(user=Depends(get_current_user)):

    with db() as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT node_id, is_active, updated_at
            FROM storage_nodes
            ORDER BY node_id
        """).fetchall()

    return [
        {
            "node_id": row["node_id"],
            "active": bool(row["is_active"]),
            "status": (
                "healthy" if row["is_active"]
                else "offline"
            ),
            "updated_at": row["updated_at"]
        }
        for row in rows
    ]


# ==========================================================
# SIMULATE NODE FAILURE / RECOVERY
# ==========================================================

@app.post("/api/storage/nodes/{node_id}/status")
def set_node_status(
    node_id: str,
    data: NodeStatusRequest,
    user=Depends(get_current_user)
):

    if node_id not in NODE_IDS:
        raise HTTPException(
            status_code=404,
            detail="Storage node not found"
        )

    with db() as conn:
        conn.execute("""
            UPDATE storage_nodes
            SET is_active = ?, updated_at = ?
            WHERE node_id = ?
        """, (
            1 if data.active else 0,
            utc_now(),
            node_id
        ))

        conn.commit()

    repair_checks = 0

    # When node returns online, repair missing replicas.
    if data.active:

        with db() as conn:
            rows = conn.execute("""
                SELECT id FROM files
            """).fetchall()

        for row in rows:
            repair_file(row["id"])
            repair_checks += 1

    return {
        "message": (
            f"{node_id} is now "
            f"{'online' if data.active else 'offline'}"
        ),
        "node_id": node_id,
        "active": data.active,
        "repair_checks_run": repair_checks
    }


# ==========================================================
# MANUAL / AUTOMATIC REPLICA REPAIR
# ==========================================================

@app.post("/api/storage/repair")
def repair_storage(user=Depends(get_current_user)):

    with db() as conn:
        rows = conn.execute("""
            SELECT id
            FROM files
            WHERE owner_id = ?
        """, (user["id"],)).fetchall()

    results = []

    for row in rows:
        results.append(
            repair_file(row["id"])
        )

    repaired_total = sum(
        item.get("repaired", 0)
        for item in results
    )

    return {
        "message": "Replica repair completed",
        "files_checked": len(results),
        "replicas_repaired": repaired_total,
        "results": results
    }


# ==========================================================
# GET REPLICAS FOR A FILE
# ==========================================================

@app.get("/api/storage/files/{file_id}/replicas")
def get_file_replicas(
    file_id: str,
    user=Depends(get_current_user)
):

    with db() as conn:
        conn.row_factory = sqlite3.Row

        row = conn.execute("""
            SELECT id, filename, checksum
            FROM files
            WHERE id = ? AND owner_id = ?
        """, (
            file_id,
            user["id"]
        )).fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="File not found"
        )

    return {
        "file_id": file_id,
        "filename": row["filename"],
        "checksum": row["checksum"],
        "replication_factor": REPLICATION_FACTOR,
        "replicas": replica_status(file_id)
    }


# ==========================================================
# CHECKSUM / INTEGRITY VERIFICATION
# ==========================================================

@app.post("/api/storage/files/{file_id}/verify")
def verify_file_integrity(
    file_id: str,
    user=Depends(get_current_user)
):

    with db() as conn:
        conn.row_factory = sqlite3.Row

        row = conn.execute("""
            SELECT id, checksum
            FROM files
            WHERE id = ? AND owner_id = ?
        """, (
            file_id,
            user["id"]
        )).fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="File not found"
        )

    expected_checksum = row["checksum"]
    results = []

    for replica in get_replica_records(file_id):

        path = Path(replica["replica_path"])

        if not path.is_file():
            results.append({
                "node_id": replica["node_id"],
                "status": "missing",
                "valid": False
            })
            continue

        try:
            content = path.read_bytes()

            actual_checksum = hashlib.sha256(
                content
            ).hexdigest()

            valid = actual_checksum == expected_checksum

            status = "healthy" if valid else "corrupt"

            results.append({
                "node_id": replica["node_id"],
                "status": status,
                "valid": valid
            })

            if not valid:
                with db() as conn:
                    conn.execute("""
                        UPDATE replicas
                        SET status = 'corrupt',
                            updated_at = ?
                        WHERE file_id = ?
                          AND node_id = ?
                    """, (
                        utc_now(),
                        file_id,
                        replica["node_id"]
                    ))

                    conn.commit()

        except OSError:
            results.append({
                "node_id": replica["node_id"],
                "status": "unreadable",
                "valid": False
            })

    all_valid = (
        all(item["valid"] for item in results)
        if results else False
    )

    return {
        "file_id": file_id,
        "integrity": (
            "verified" if all_valid
            else "degraded"
        ),
        "expected_checksum": expected_checksum,
        "replicas": results
    }


# ==========================================================
# STORAGE SUMMARY
# ==========================================================

@app.get("/api/storage/summary")
def storage_summary(user=Depends(get_current_user)):
    return storage_health(user)
