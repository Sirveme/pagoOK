"""API publica del webhook + admin de dispositivos + API publica v1."""
from app.api.notificaciones import router as webhook_router
from app.api.admin_dispositivos import router as admin_dispositivos_router
from app.api.publica_v1 import router as api_v1_publica_router

__all__ = ["webhook_router", "admin_dispositivos_router", "api_v1_publica_router"]