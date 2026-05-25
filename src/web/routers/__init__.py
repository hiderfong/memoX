"""Routers package"""
from .auth import router as auth_router
from .chat import router as chat_router
from .documents import router as documents_router
from .imaging import router as imaging_router
from .memories import router as memories_router
from .scheduled import router as scheduled_router
from .skills import router as skills_router
from .system import router as system_router
from .tasks import router as tasks_router
from .workers import router as workers_router
from .workflows import router as workflows_router

__all__ = [
    "auth_router",
    "chat_router",
    "documents_router",
    "imaging_router",
    "memories_router",
    "scheduled_router",
    "skills_router",
    "system_router",
    "tasks_router",
    "workers_router",
    "workflows_router",
]
