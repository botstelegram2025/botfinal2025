# services/whatsapp_service.py
import os
import time
import logging
from typing import Optional, Dict, Any
import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = (5, 20)  # (connect, read) segundos
POLL_TOTAL_TIME = 25       # total de tempo para polling de QR
POLL_INTERVAL = 2          # intervalo entre polls (s)

class WhatsAppService:
    def __init__(self):
        base = os.getenv("WHATSAPP_SERVICE_URL", "").strip().rstrip("/")
        if not base:
            raise RuntimeError("WHATSAPP_SERVICE_URL não definido")
        # NUNCA anexe :3000 aqui — Railway já serve na porta padrão (443/80)
        self.base_url = base

        self.api_token = os.getenv("WHATSAPP_API_TOKEN", "").strip() or None
        self.session_id = os.getenv("WHATSAPP_SESSION_ID", "").strip() or None

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_token:
            h["x-api-token"] = self.api_token
        return h

    def _url(self, path: str) -> str:
        path = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url}{path}"

    def status(self) -> Dict[str, Any]:
        params = {}
        if self.session_id:
            params["sessionId"] = self.session_id
        try:
            r = requests.get(self._url("/status"), headers=self._headers(), params=params, timeout=DEFAULT_TIMEOUT)
            r.raise_for_status()
            return {"success": True, "data": r.json()}
        except Exception as e:
            logger.error(f"WhatsApp status error: {e}")
            return {"success": False, "error": str(e)}

    def get_qr(self) -> Dict[str, Any]:
        params = {}
        if self.session_id:
            params["sessionId"] = self.session_id
        try:
            r = requests.get(self._url("/qr"), headers=self._headers(), params=params, timeout=DEFAULT_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            # formatos comuns: {"ok":true,"qr":"..."} ou {"qr":"..."} ou {"data":{"qr":"..."}}
            qr = data.get("qr") or (data.get("data") or {}).get("qr")
            if qr:
                return {"success": True, "qr": qr, "raw": data}
            return {"success": False, "error": "No QR in response", "raw": data}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _try_post(self, path: str, json: Optional[dict] = None, params: Optional[dict] = None) -> Optional[requests.Response]:
        try:
            r = requests.post(self._url(path), headers=self._headers(), json=json, params=params, timeout=DEFAULT_TIMEOUT)
            if 200 <= r.status_code < 300:
                return r
            logger.warning(f"POST {path} -> {r.status_code} {r.text[:180]}")
            return None
        except Exception as e:
            logger.warning(f"POST {path} failed: {e}")
            return None

    def reconnect(self) -> Dict[str, Any]:
        # Alguns serviços usam /reconnect, outros /restart
        params = {}
        if self.session_id:
            params["sessionId"] = self.session_id

        tried = []

        for path in ("/reconnect", "/restart"):
            tried.append(path)
            res = self._try_post(path, params=params)
            if res is not None:
                try:
                    return {"success": True, "data": res.json()}
                except Exception:
                    return {"success": True, "data": {"status": "ok"}}

        return {"success": False, "error": f"No reconnect endpoint worked. Tried: {', '.join(tried)}"}

    def force_qr(self) -> Dict[str, Any]:
        """
        Fluxo robusto:
        1) Tenta endpoints de forçar QR (variações).
        2) Em seguida, faz polling de /qr até vir um código (ou timeout).
        """
        params = {}
        if self.session_id:
            params["sessionId"] = self.session_id

        # 1) Tentar variações de "force"
        variants = [
            ("/force-qr", None),
            ("/force-qr/{}".format(self.session_id), None) if self.session_id else None,
            ("/qr/force", None),
        ]
        variants = [v for v in variants if v]

        forced = False
        tried = []
        for path, body in variants:
            tried.append(path)
            res = self._try_post(path, json=body, params=params)
            if res is not None:
                forced = True
                break

        if not forced:
            # Se não tem endpoint de force, tenta pelo menos reconectar
            rec = self.reconnect()
            if not rec.get("success"):
                return {"success": False, "error": f"Force QR failed and reconnect failed: {rec.get('error')}",
                        "details": {"tried": tried}}

        # 2) Polling de /qr
        started = time.time()
        while time.time() - started < POLL_TOTAL_TIME:
            qr = self.get_qr()
            if qr.get("success") and qr.get("qr"):
                return {"success": True, "qr": qr["qr"], "raw": qr.get("raw")}
            time.sleep(POLL_INTERVAL)

        return {"success": False, "error": "QR polling timeout"}

    def send_message(self, phone: str, message: str) -> Dict[str, Any]:
        payload = {"phone": phone, "message": message}
        if self.session_id:
            payload["sessionId"] = self.session_id
        try:
            r = requests.post(self._url("/send"), headers=self._headers(), json=payload, timeout=DEFAULT_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            ok = bool(data.get("ok") or data.get("success"))
            return {"success": ok, "data": data, "error": None if ok else data}
        except Exception as e:
            return {"success": False, "error": str(e)}

# Instância singleton
whatsapp_service = WhatsAppService()
