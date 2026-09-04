"""
M.I.R.A. SQLAlchemy Database Infrastructure and Relational Models.
"""

from datetime import datetime, timezone
import os
import time
import json
from typing import Any, Generator, List, Optional

class MockColumnComparison:
    def __init__(self, column_name, operator, value):
        self.column_name = column_name
        self.operator = operator
        self.value = value
    def __or__(self, other):
        return self
    def __and__(self, other):
        return self

try:
    from sqlalchemy import (
        Boolean,
        Column,
        DateTime,
        Float,
        ForeignKey,
        Integer,
        String,
        Text,
        create_engine,
    )
    from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker, Session
    has_sqlalchemy = True
except ImportError:
    has_sqlalchemy = False
    class BaseFallback:
        metadata = type("Metadata", (), {"create_all": lambda *a, **k: None})()
        def __init__(self, **kwargs):
            # Seed default None values for MockColumns to prevent fallback attribute lookup returning MockColumn instances
            for name, attr in list(self.__class__.__dict__.items()):
                if isinstance(attr, MockColumn):
                    setattr(self, name, None)
            for key, value in kwargs.items():
                setattr(self, key, value)
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__(**kwargs)
            for name, attr in list(cls.__dict__.items()):
                if isinstance(attr, MockColumn):
                    attr.name = name
    DeclarativeBase = BaseFallback
    def create_engine(*args, **kwargs): return None
    def sessionmaker(*args, **kwargs): return lambda: None

    class MockColumn:
        def __init__(self, name=None):
            self.name = name
        def asc(self, *args, **kwargs): return self
        def desc(self, *args, **kwargs): return self
        def in_(self, other):
            return MockColumnComparison(self.name, "in", other)
        def __eq__(self, other):
            return MockColumnComparison(self.name, "eq", other)
        def __ne__(self, other):
            return MockColumnComparison(self.name, "ne", other)
        def __gt__(self, other): return False
        def __lt__(self, other): return False
        def __ge__(self, other): return False
        def __le__(self, other): return False
        def __getattr__(self, name): return lambda *a, **k: self

    def Column(*args, **kwargs): return MockColumn()
    def relationship(*args, **kwargs): return None
    def Integer(*args, **kwargs): return None
    def String(*args, **kwargs): return None
    def Boolean(*args, **kwargs): return None
    def DateTime(*args, **kwargs): return None
    def Float(*args, **kwargs): return None
    def Text(*args, **kwargs): return None
    def ForeignKey(*args, **kwargs): return None
    Session = Any

# SQLite Database URL
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mira.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

if has_sqlalchemy:
    try:
        engine = create_engine(
            SQLALCHEMY_DATABASE_URL,
            connect_args={"check_same_thread": False},
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    except Exception:
        engine = None
        SessionLocal = lambda: None
else:
    engine = None
    SessionLocal = lambda: None


class Base(DeclarativeBase):
    """Base declarative class for all database models."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class User(Base):
    """User account model for authentication and RBAC authorization."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="user", nullable=False)  # "admin" or "user"
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    credentials = relationship("Credentials", back_populates="user", cascade="all, delete-orphan")
    field_mappings = relationship("FieldMapping", back_populates="user", cascade="all, delete-orphan")


class Credentials(Base):
    """Encrypted/stored credentials for external integrations (Monday, Xero)."""

    __tablename__ = "credentials"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    platform_name = Column(String, nullable=False, index=True)  # "monday" or "xero"
    api_key = Column(String, nullable=True)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expiry = Column(Float, nullable=True)
    board_id = Column(String, nullable=True)

    user = relationship("User", back_populates="credentials")


PlatformCredential = Credentials


class FieldMapping(Base):
    """Custom schema field mappings connecting Monday columns to Xero target paths."""

    __tablename__ = "field_mappings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    source_column = Column(String, nullable=False)
    target_xero_path = Column(String, nullable=False)
    custom_override_path = Column(String, nullable=True)
    mapping_version = Column(String, default="v1.0", nullable=False)
    board_id = Column(String, nullable=True, index=True)

    user = relationship("User", back_populates="field_mappings")


class SyncLog(Base):
    """System audit log recording transaction synchronization events."""

    __tablename__ = "sync_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    status = Column(String, nullable=False, index=True)  # "SUCCESS", "FAILURE", "PARTIAL"
    direction = Column(String, nullable=True, index=True)  # "xero_to_monday" or "monday_to_xero"
    payload_count = Column(Integer, default=0, nullable=False)
    error_details = Column(Text, nullable=True)


# Fallback Mock Session for offline environments
class MockQuery:
    def __init__(self, items, db=None):
        self.items = items or []
        self.db = db

    def filter(self, *args, **kwargs):
        def evaluate_condition(item, cond):
            if not isinstance(cond, MockColumnComparison):
                return True
            if getattr(cond, "is_or", False):
                return evaluate_condition(item, cond.left) or evaluate_condition(item, cond.right)
            
            col = getattr(cond, "column_name", None)
            op = getattr(cond, "operator", None)
            val = getattr(cond, "value", None)
            if not col or not op:
                return True
                
            item_val = getattr(item, col, None)
            if op == "eq":
                if isinstance(item_val, int) and isinstance(val, str):
                    try: val = int(val)
                    except ValueError: pass
                elif isinstance(item_val, str) and isinstance(val, int):
                    try: item_val = int(item_val)
                    except ValueError: pass
                return item_val == val
            elif op == "ne":
                return item_val != val
            elif op == "in":
                in_list = val if isinstance(val, (list, tuple, set)) else [val]
                return item_val in in_list
            return True

        filtered_items = list(self.items)
        for cond in args:
            filtered_items = [item for item in filtered_items if evaluate_condition(item, cond)]
        return MockQuery(filtered_items, db=self.db)

    def order_by(self, *args, **kwargs): return self
    def limit(self, *args, **kwargs): return self
    def first(self):
        return self.items[0] if self.items else None
    def all(self):
        return self.items
    def delete(self, synchronize_session=False):
        if self.db:
            for item in self.items:
                self.db.delete(item)

class MockDBSession:
    def __init__(self, file_path="mock_db.json"):
        import sys
        is_testing = "unittest" in sys.modules or os.getenv("TESTING") == "True"
        if is_testing:
            file_path = "mock_db_test.json"
        self.file_path = os.path.abspath(file_path)
        self.data = self._load_data()
        self._active_instances = []

    def _load_data(self):
        if os.path.exists(self.file_path) and os.path.getsize(self.file_path) > 0:
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"credentials": [], "users": [], "field_mappings": [], "sync_logs": []}

    def _save_data(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            from utils.logger import logger
            logger.error("MockDBSession save error: %s", e)

    def query(self, model):
        table_name = getattr(model, "__tablename__", None)
        if not table_name:
            return MockQuery([], db=self)

        if table_name not in self.data:
            self.data[table_name] = []

        if table_name == "users" and not self.data["users"]:
            from utils.auth import hash_password
            admin_dict = {
                "id": 1,
                "email": "admin@mira.com",
                "hashed_password": hash_password("Admin123!"),
                "role": "admin",
                "is_active": True,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            self.data["users"].append(admin_dict)
            self._save_data()

        instances = []
        for x in self.data[table_name]:
            inst = model()
            for k, v in x.items():
                if k in ("created_at", "timestamp") and isinstance(v, str):
                    try:
                        v = datetime.fromisoformat(v)
                    except ValueError:
                        v = datetime.now(timezone.utc)
                setattr(inst, k, v)
            instances.append(inst)

        for inst in instances:
            if not any(active.id == inst.id and type(active) is type(inst) for active in self._active_instances):
                self._active_instances.append(inst)

        return MockQuery(instances, db=self)

    def add(self, item):
        if item not in self._active_instances:
            self._active_instances.append(item)

    def commit(self):
        for item in self._active_instances:
            table_name = getattr(item, "__tablename__", None)
            if not table_name:
                continue

            item_dict = {}
            for k, v in item.__dict__.items():
                if k.startswith("_"):
                    continue
                # Skip relationships and lists/dicts
                if isinstance(v, (list, dict, set)) or hasattr(v, "__table__") or hasattr(v, "__tablename__"):
                    continue
                if isinstance(v, datetime):
                    v = v.isoformat()
                if isinstance(v, (str, int, float, bool, type(None))):
                    item_dict[k] = v

            if "id" not in item_dict or not item_dict["id"]:
                existing = self.data.get(table_name, [])
                item_dict["id"] = max([x.get("id", 0) for x in existing] + [0]) + 1
                item.id = item_dict["id"]

            existing = self.data.get(table_name, [])
            updated = False
            for idx, x in enumerate(existing):
                if x.get("id") == item_dict["id"]:
                    existing[idx] = item_dict
                    updated = True
                    break
            if not updated:
                existing.append(item_dict)

            self.data[table_name] = existing

        self._save_data()

    def delete(self, item):
        self._active_instances = [x for x in self._active_instances if x is not item]
        table_name = getattr(item, "__tablename__", None)
        if table_name and hasattr(item, "id"):
            existing = self.data.get(table_name, [])
            self.data[table_name] = [x for x in existing if x.get("id") != item.id]
            self._save_data()

    def close(self):
        pass


def init_db() -> None:
    """Creates database tables if they do not exist and performs migration."""
    if has_sqlalchemy and engine:
        try:
            Base.metadata.create_all(bind=engine)
            
            # Check if board_id column exists in field_mappings table
            with engine.connect() as conn:
                from sqlalchemy import text
                result = conn.execute(text("PRAGMA table_info(field_mappings)")).fetchall()
                column_names = [row[1] for row in result]
                if "board_id" not in column_names:
                    conn.execute(text("ALTER TABLE field_mappings ADD COLUMN board_id VARCHAR"))
                    try:
                        conn.commit()
                    except Exception:
                        pass

                # Check if direction column exists in sync_logs table
                result_logs = conn.execute(text("PRAGMA table_info(sync_logs)")).fetchall()
                column_names_logs = [row[1] for row in result_logs]
                if "direction" not in column_names_logs:
                    conn.execute(text("ALTER TABLE sync_logs ADD COLUMN direction VARCHAR"))
                    try:
                        conn.commit()
                    except Exception:
                        pass
        except Exception:
            pass


def get_db() -> Generator[Any, None, None]:
    """FastAPI dependency yielding a database session."""
    db = None
    if has_sqlalchemy and SessionLocal:
        try:
            db = SessionLocal()
        except Exception:
            db = None
    if not db:
        db = MockDBSession()
    try:
        yield db
    finally:
        if hasattr(db, "close"):
            db.close()
