"""API publica del webhook + admin de dispositivos."""
from app.api.notificaciones import router as webhook_router
from app.api.admin_dispositivos import router as admin_dispositivos_router

__all__ = ["webhook_router", "admin_dispositivos_router"]
