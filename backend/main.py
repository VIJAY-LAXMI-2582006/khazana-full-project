from fastapi import (

    FastAPI, UploadFile, File, HTTPException, Depends

)

from fastapi.middleware.cors import CORSMiddleware

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from fastapi.responses import FileResponse

from pydantic import BaseModel, EmailStr

from pwdlib import PasswordHash

from pathlib import Path

from datetime import datetime, timedelta, timezone

import hashlib

import sqlite3

import uuid

import jwt

import os





app = FastAPI(title="Khazana Storage API")



app.add_middleware(

    CORSMiddleware,

    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):[0-9]+$",
    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],

)



BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"

DATA_DIR.mkdir(exist_ok=True)



DB_PATH = BASE_DIR / "vault.db"



# Development secret only. Set a strong environment variable

# before deploying this app publicly.

SECRET_KEY = os.getenv(

    "KHAZANA_SECRET_KEY",

    "dev-only-change-this-secret-before-deployment"

)

ALGORITHM = "HS256"

TOKEN_EXPIRE_MINUTES = 60 * 24



password_hash = PasswordHash.recommended()

security = HTTPBearer()





# ---------------- DATABASE ----------------



def init_db():

    with sqlite3.connect(DB_PATH) as conn:



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





        # Migrate existing files table without deleting old data.

        columns = [

            row[1]

            for row in conn.execute(

                "PRAGMA table_info(files)"

            ).fetchall()

        ]



        if "owner_id" not in columns:

            conn.execute("""

                ALTER TABLE files

                ADD COLUMN owner_id TEXT REFERENCES users(id)

            """)

        user_columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
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

        conn.commit()





init_db()





# ---------------- REQUEST MODELS ----------------



class RegisterRequest(BaseModel):

    name: str

    email: EmailStr

    password: str





class LoginRequest(BaseModel):

    email: EmailStr

    password: str





# ---------------- AUTH HELPERS ----------------



def create_access_token(user_id: str):

    expire = datetime.now(timezone.utc) + timedelta(

        minutes=TOKEN_EXPIRE_MINUTES

    )



    payload = {

        "sub": user_id,

        "exp": expire,

    }



    return jwt.encode(

        payload,

        SECRET_KEY,

        algorithm=ALGORITHM

    )





def get_current_user(

    credentials: HTTPAuthorizationCredentials = Depends(security)

):

    token = credentials.credentials



    try:

        payload = jwt.decode(

            token,

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



    with sqlite3.connect(DB_PATH) as conn:

        conn.row_factory = sqlite3.Row



        user = conn.execute(

            """

            SELECT id, name, email, created_at

            FROM users

            WHERE id = ?

            """,

            (user_id,)

        ).fetchone()



    if not user:

        raise HTTPException(

            status_code=401,

            detail="User not found"

        )



    return dict(user)





# ---------------- BASIC ROUTES ----------------



@app.get("/")

def home():

    return {"message": "Khazana API is running"}





@app.get("/health")

def health():

    return {"status": "healthy", "service": "Khazana"}





# ---------------- REGISTER ----------------



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

        with sqlite3.connect(DB_PATH) as conn:

            conn.execute(

                """

                INSERT INTO users

                (id, name, email, password_hash, created_at)

                VALUES (?, ?, ?, ?, ?)

                """,

                (

                    user_id,

                    name,

                    email,

                    hashed_password,

                    datetime.now(timezone.utc).isoformat(),

                )

            )

            conn.commit()



    except sqlite3.IntegrityError:

        raise HTTPException(

            status_code=409,

            detail="Email is already registered"

        )



    token = create_access_token(user_id)



    return {

        "message": "Account created successfully",

        "access_token": token,

        "token_type": "bearer",

        "user": {

            "id": user_id,

            "name": name,

            "email": email,

        }

    }





# ---------------- LOGIN ----------------



@app.post("/api/login")

def login(data: LoginRequest):



    email = data.email.lower().strip()



    with sqlite3.connect(DB_PATH) as conn:

        conn.row_factory = sqlite3.Row



        user = conn.execute(

            """

            SELECT id, name, email, password_hash

            FROM users

            WHERE email = ?

            """,

            (email,)

        ).fetchone()



    if not user or not password_hash.verify(

        data.password,

        user["password_hash"]

    ):

        raise HTTPException(

            status_code=401,

            detail="Incorrect email or password"

        )



    token = create_access_token(user["id"])



    return {

        "message": "Login successful",

        "access_token": token,

        "token_type": "bearer",

        "user": {

            "id": user["id"],

            "name": user["name"],

            "email": user["email"],

        }

    }





# ---------------- CURRENT USER ----------------



@app.get("/api/me")

def get_me(user=Depends(get_current_user)):

    return user





# ---------------- UPLOAD FILE ----------------



@app.post("/api/upload")

async def upload_file(

    file: UploadFile = File(...),

    user=Depends(get_current_user)

):



    file_id = str(uuid.uuid4())

    content = await file.read()



    if not content:

        raise HTTPException(

            status_code=400,

            detail="Uploaded file is empty"

        )



    checksum = hashlib.sha256(content).hexdigest()



    # Each user has their own directory.

    user_dir = DATA_DIR / user["id"]

    user_dir.mkdir(parents=True, exist_ok=True)



    stored_path = user_dir / file_id



    try:

        stored_path.write_bytes(content)



        with sqlite3.connect(DB_PATH) as conn:

            conn.execute(

                """

                INSERT INTO files

                (id, filename, size, checksum, stored_path, owner_id)

                VALUES (?, ?, ?, ?, ?, ?)

                """,

                (

                    file_id,

                    file.filename or "unnamed",

                    len(content),

                    checksum,

                    str(stored_path),

                    user["id"],

                )

            )

            conn.commit()



        return {

            "message": "File uploaded successfully",

            "id": file_id,

            "filename": file.filename,

            "size": len(content),

            "checksum": checksum,

            "status": "stored",

        }



    except Exception as e:

        stored_path.unlink(missing_ok=True)

        raise HTTPException(

            status_code=500,

            detail=f"Upload failed: {str(e)}"

        )





# ---------------- LIST USER FILES ----------------



@app.get("/api/files")

def list_files(user=Depends(get_current_user)):



    with sqlite3.connect(DB_PATH) as conn:

        conn.row_factory = sqlite3.Row



        rows = conn.execute(

            """

            SELECT id, filename, size, checksum

            FROM files

            WHERE owner_id = ?

            ORDER BY rowid DESC

            """,

            (user["id"],)

        ).fetchall()



    return [dict(row) for row in rows]





# ---------------- OPEN / DOWNLOAD FILE ----------------



@app.get("/api/files/{file_id}/open")

def open_file(

    file_id: str,

    user=Depends(get_current_user)

):



    with sqlite3.connect(DB_PATH) as conn:

        conn.row_factory = sqlite3.Row



        row = conn.execute(

            """

            SELECT filename, stored_path

            FROM files

            WHERE id = ? AND owner_id = ?

            """,

            (file_id, user["id"])

        ).fetchone()



    if not row:

        raise HTTPException(

            status_code=404,

            detail="File not found"

        )



    path = Path(row["stored_path"]).resolve()



    # Ensure the file is within this user's storage directory.

    user_dir = (DATA_DIR / user["id"]).resolve()



    if not path.is_relative_to(user_dir):

        raise HTTPException(

            status_code=403,

            detail="Invalid file location"

        )



    if not path.is_file():

        raise HTTPException(

            status_code=404,

            detail="Stored file is missing"

        )



    return FileResponse(

        path=str(path),

        filename=row["filename"],

        content_disposition_type="inline",

    )
