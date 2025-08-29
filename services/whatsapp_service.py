import os
import time
import logging
from typing import Dict, Any, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Timeouts (connect, read)
DEFAULT_TIMEOUT: Tuple[int, int] = (5, 25)
POLL_TOTAL_TIME = 25  # tempo total para polling do QR
POLL_INTERVAL = 2     # intervalo entre tentativas de QR


class WhatsAppService:
    def __init__(self):
        """
        Inicializa o cliente do serviço WhatsApp (Baileys Gateway).

        Variáveis suportadas:
        - WHATSAPP_SERVICE_URL  -> URL base do gateway (Railway: usar a URL pública, sem porta).
        - WHATSAPP_API_TOKEN    -> (opcional) token para header x-api-token.
        - WHATSAPP_SESSION_ID   -> (opcional) id de sessão para multi-sessão.
        """
        base = os.getenv("WHATSAPP_SERVICE_URL", "").strip().rstrip("/")
        railway = os.getenv("RAILWAY_ENVIRONMENT_NAME")

        if base:
            self.baileys_url = base
        elif railway:
            # Comunicação interna em Railway (se o serviço estiver no mesmo container/rede)
            self.baileys_url = "http://127.0.0.1:3001"
        else:
            # Desenvolvimento local
            self.baileys_url = "http://localhost:3001"

        self.api_token: Optional[str] = os.getenv("WHATSAPP_API_TOKEN") or None
        self.session_id: Optional[str] = os.getenv("WHATSAPP_SESSION_ID") or None

        self.headers = {"Content-Type": "application/json"}
        if self.api_token:
            self.headers["x-api-token"] = self.api_token

        logger.info(
            f"WhatsApp Service initialized with URL: {self.baileys_url} | "
            f"session_id={self.session_id or '-'}"
        )

    # ---------------------------- Helpers ----------------------------

    def _url(self, path: str) -> str:
        path = path if path.startswith("/") else f"/{path}"
        return f"{self.baileys_url}{path}"

    def _get(
        self,
        path: str,
        params: Optional[dict] = None,
        timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
    ) -> requests.Response:
        return requests.get(self._url(path), headers=self.headers, params=params or {}, timeout=timeout)

    def _post(
        self,
        path: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
    ) -> requests.Response:
        return requests.post(self._url(path), headers=self.headers, json=json or {}, params=params or {}, timeout=timeout)

    # ---------------------------- Saúde / Status ----------------------------

    def get_health_status(self) -> Dict[str, Any]:
        """GET /health."""
        try:
            r = self._get("/health", timeout=(5, 10))
            if 200 <= r.status_code < 300:
                return r.json()
            return {"success": False, "error": f"HTTP {r.status_code}", "details": r.text}
        except Exception as e:
            logger.error(f"Error getting WhatsApp health status: {e}")
            return {"success": False, "error": "Health check failed", "details": str(e)}

    def check_instance_status(self, user_id: int) -> Dict[str, Any]:
        """
        Verifica status da instância:
        1) /status/{user_id}
        2) fallback: /status?sessionId=
        """
        # 1) preferir status por user_id (alguns gateways isolam por id)
        try:
            r = self._get(f"/status/{user_id}", timeout=(5, 20))
            if 200 <= r.status_code < 300:
                data = r.json()
                return {
                    "success": True,
                    "connected": bool(data.get("connected")),
                    "state": data.get("state", "unknown"),
                    "qrCode": data.get("qr") or data.get("qrCode"),
                    "response": data,
                }
        except Exception:
            pass

        # 2) fallback: status geral com sessionId
        try:
            params = {}
            if self.session_id:
                params["sessionId"] = self.session_id
            r2 = self._get("/status", params=params, timeout=(5, 20))
            if 200 <= r2.status_code < 300:
                data2 = r2.json()
                # alguns gateways aninham em "data"
                d = data2.get("data") if isinstance(data2.get("data"), dict) else data2
                return {
                    "success": True,
                    "connected": bool(d.get("connected")),
                    "state": d.get("state", "unknown"),
                    "qrCode": d.get("qr") or d.get("qrCode"),
                    "response": d,
                }
            return {"success": False, "error": f"HTTP {r2.status_code}", "details": r2.text}
        except requests.exceptions.ConnectionError:
            logger.error("Baileys server not running")
            return {
                "success": False,
                "error": "Baileys server not running",
                "details": "Please start the Baileys server",
            }
        except Exception as e:
            logger.error(f"Error checking WhatsApp instance status: {e}")
            return {"success": False, "error": "Status check failed", "details": str(e)}

    # ---------------------------- QR / Conexão ----------------------------

    def get_qr_code(self, user_id: int) -> Dict[str, Any]:
        """
        Obtém QR:
        1) tenta /qr (com sessionId, se houver)
        2) fallback: /status (muitos gateways retornam qr/qrCode lá)
        """
        # 1) /qr
        try:
            params = {}
            if self.session_id:
                params["sessionId"] = self.session_id
            r = self._get("/qr", params=params, timeout=(5, 20))
            if 200 <= r.status_code < 300:
                data = r.json()
                qr = data.get("qr") or (data.get("data") or {}).get("qr") or data.get("qrCode")
                if qr:
                    return {"success": True, "qrCode": qr, "raw": data}
        except Exception:
            pass

        # 2) /status
        st = self.check_instance_status(user_id)
        if st.get("success") and st.get("qrCode"):
            return {"success": True, "qrCode": st["qrCode"], "state": st.get("state"), "connected": st.get("connected")}
        return {"success": False, "error": "QR Code not available"}

    def reconnect_whatsapp(self, user_id: int) -> Dict[str, Any]:
        """
        Reabre sessão:
        - tenta /reconnect/{id}
        - fallback: /reconnect (com sessionId) /restart
        """
        # /reconnect/{id}
        try:
            r = self._post(f"/reconnect/{user_id}", timeout=(5, 25))
            if 200 <= r.status_code < 300:
                return {"success": True, "data": r.json()}
        except Exception:
            pass

        # fallback: /reconnect?sessionId=  OU /restart
        params = {}
        if self.session_id:
            params["sessionId"] = self.session_id
        for path in ("/reconnect", "/restart"):
            try:
                r2 = self._post(path, params=params, timeout=(5, 25))
                if 200 <= r2.status_code < 300:
                    return {"success": True, "data": r2.json()}
            except Exception:
                continue

        return {"success": False, "error": "No reconnect endpoint worked"}

    def disconnect_whatsapp(self, user_id: int) -> Dict[str, Any]:
        """Desconecta a sessão (tenta /disconnect/{id} e /disconnect?sessionId=)."""
        try:
            r = self._post(f"/disconnect/{user_id}", timeout=(5, 20))
            if 200 <= r.status_code < 300:
                return r.json()
        except Exception:
            pass

        try:
            params = {}
            if self.session_id:
                params["sessionId"] = self.session_id
            r2 = self._post("/disconnect", params=params, timeout=(5, 20))
            if 200 <= r2.status_code < 300:
                return r2.json()
            return {"success": False, "error": f"HTTP {r2.status_code}", "details": r2.text}
        except Exception as e:
            logger.error(f"Error disconnecting WhatsApp: {e}")
            return {"success": False, "error": "Disconnect failed", "details": str(e)}

    def force_new_qr(self, user_id: int) -> Dict[str, Any]:
        """
        Força novo QR:
        - tenta /force-qr/{id}
        - fallback: /force-qr?sessionId=  ou  /qr/force
        - faz polling do QR.
        """
        # tentar variantes de force
        tried = []

        def _try_force(path: str, params: Optional[dict] = None) -> bool:
            tried.append(path)
            try:
                r = self._post(path, params=params, timeout=(5, 45))
                return 200 <= r.status_code < 300
            except Exception as e:
                logger.warning(f"POST {path} failed: {e}")
                return False

        if _try_force(f"/force-qr/{user_id}"):
            pass
        else:
            params = {"sessionId": self.session_id} if self.session_id else None
            if not _try_force("/force-qr", params=params) and not _try_force("/qr/force", params=params):
                rec = self.reconnect_whatsapp(user_id)
                if not rec.get("success"):
                    return {"success": False, "error": "Force QR failed and reconnect failed", "details": {"tried": tried}}

        # polling do QR
        started = time.time()
        while time.time() - started < POLL_TOTAL_TIME:
            qr = self.get_qr_code(user_id)
            if qr.get("success") and qr.get("qrCode"):
                return qr
            time.sleep(POLL_INTERVAL)

        return {"success": False, "error": "QR polling timeout"}

    # ---------------------------- Envio de mensagens ----------------------------

    @staticmethod
    def _normalize_br_phone(phone_number: str) -> str:
        """Normaliza telefone para E.164 (Brasil)."""
        digits = "".join(ch for ch in phone_number if ch.isdigit())
        if not digits.startswith("55"):
            digits = "55" + digits
        return digits

    def send_message(self, phone_number: str, message: str, user_id: int) -> Dict[str, Any]:
        """
        Envia WhatsApp com isolamento por usuário.
        Tenta:
          1) POST /send (com sessionId no payload)
             - payload com chave 'phone'
             - fallback payload com chave 'number'
          2) POST /send/{user_id}
        """
        try:
            phone = self._normalize_br_phone(phone_number)

            # (1) /send com sessionId
            payload_phone = {"phone": phone, "message": message}
            payload_number = {"number": phone, "message": message}

            if self.session_id:
                payload_phone["sessionId"] = self.session_id
                payload_number["sessionId"] = self.session_id

            # tenta payload com "phone"
            try:
                r1 = self._post("/send", json=payload_phone, timeout=(5, 45))
                if 200 <= r1.status_code < 300:
                    data = r1.json()
                    ok = bool(data.get("success") or data.get("ok"))
                    if ok:
                        logger.info(f"WhatsApp message sent to {phone}")
                        return {"success": True, "message_id": data.get("messageId"), "response": data}
                else:
                    logger.debug(f"/send -> HTTP {r1.status_code}: {r1.text[:200]}")
            except Exception as e:
                logger.debug(f"/send(phone) error: {e}")

            # fallback: payload com "number"
            try:
                r1b = self._post("/send", json=payload_number, timeout=(5, 45))
                if 200 <= r1b.status_code < 300:
                    data = r1b.json()
                    ok = bool(data.get("success") or data.get("ok"))
                    if ok:
                        logger.info(f"WhatsApp message sent to {phone}")
                        return {"success": True, "message_id": data.get("messageId"), "response": data}
                else:
                    logger.debug(f"/send -> HTTP {r1b.status_code}: {r1b.text[:200]}")
            except Exception as e:
                logger.debug(f"/send(number) error: {e}")

            # (2) /send/{user_id}
            try:
                r2 = self._post(f"/send/{user_id}", json=payload_phone, timeout=(5, 45))
                if 200 <= r2.status_code < 300:
                    data2 = r2.json()
                    ok2 = bool(data2.get("success") or data2.get("ok"))
                    if ok2:
                        logger.info(f"WhatsApp message sent to {phone} (via /send/{{id}})")
                        return {"success": True, "message_id": data2.get("messageId"), "response": data2}
                else:
                    logger.debug(f"/send/{user_id} -> HTTP {r2.status_code}: {r2.text[:200]}")
            except Exception as e:
                logger.debug(f"/send/{{id}}(phone) error: {e}")

            # fallback /send/{user_id} com "number"
            try:
                r2b = self._post(f"/send/{user_id}", json=payload_number, timeout=(5, 45))
                if 200 <= r2b.status_code < 300:
                    data2b = r2b.json()
                    ok2b = bool(data2b.get("success") or data2b.get("ok"))
                    if ok2b:
                        logger.info(f"WhatsApp message sent to {phone} (via /send/{{id}} number)")
                        return {"success": True, "message_id": data2b.get("messageId"), "response": data2b}
                else:
                    logger.debug(f"/send/{user_id} -> HTTP {r2b.status_code}: {r2b.text[:200]}")
            except Exception as e:
                logger.debug(f"/send/{{id}}(number) error: {e}")

            # Se chegou aqui, falhou
            logger.error("Failed to send WhatsApp message: no endpoint accepted payload")
            return {"success": False, "error": "Send failed (no endpoint accepted payload)"}

        except requests.exceptions.Timeout:
            logger.error("WhatsApp API timeout")
            return {"success": False, "error": "Timeout", "details": "API request timed out"}
        except requests.exceptions.RequestException as e:
            logger.error(f"WhatsApp API request error: {e}")
            return {"success": False, "error": "Request failed", "details": str(e)}
        except Exception as e:
            logger.error(f"Unexpected error sending WhatsApp message: {e}")
            return {"success": False, "error": "Unexpected error", "details": str(e)}

    # ---------------------------- Pairing code (opcional) ----------------------------

    def request_pairing_code(self, user_id: int, phone_number: str) -> Dict[str, Any]:
        """POST /pairing-code/{id} (se o gateway suportar)."""
        try:
            payload = {"phoneNumber": phone_number}
            r = self._post(f"/pairing-code/{user_id}", json=payload, timeout=(5, 45))
            if 200 <= r.status_code < 300:
                data = r.json()
                if data.get("success") or data.get("pairingCode"):
                    return {
                        "success": True,
                        "pairing_code": data.get("pairingCode") or data.get("code"),
                        "response": data,
                    }
                return {"success": False, "error": data.get("error") or "Unknown error", "details": data}
            return {"success": False, "error": f"HTTP {r.status_code}", "details": r.text}
        except requests.exceptions.Timeout:
            logger.error("Timeout requesting pairing code")
            return {"success": False, "error": "Timeout requesting pairing code"}
        except Exception as e:
            logger.error(f"Error requesting pairing code for user {user_id}: {e}")
            return {"success": False, "error": str(e)}

    def get_pairing_code(self, user_id: int) -> Dict[str, Any]:
        """GET /pairing-code/{id} (se existir)."""
        try:
            r = self._get(f"/pairing-code/{user_id}", timeout=(5, 20))
            if 200 <= r.status_code < 300:
                return r.json()
            return {"success": False, "error": f"HTTP {r.status_code}", "details": r.text}
        except Exception as e:
            logger.error(f"Error getting pairing code for user {user_id}: {e}")
            return {"success": False, "error": str(e)}

    # ---------------------------- Utilitário de template ----------------------------

    def format_message(self, template: str, **kwargs) -> str:
        """Formata template com str.format(**kwargs)."""
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.error(f"Missing template variable: {e}")
            return template
        except Exception as e:
            logger.error(f"Error formatting message template: {e}")
            return template


# Instância global
whatsapp_service = WhatsAppService()
