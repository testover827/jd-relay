"""API routes for JD-Relay Forwarder."""

from .dingtalk import router as dingtalk_router

__all__ = ["dingtalk_router"]
