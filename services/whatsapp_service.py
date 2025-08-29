# services/whatsapp_service.py

import os
import time
import json
import logging
from typing import Dict, Any, Optional

import requests

logger = logging.getLogger(__name__)


class WhatsAppService:
    def __init__(self):
        """
        Inicializa o cliente para o gateway Baileys (WhatsApp).
        Prioriza WHATSAPP_SERVICE_URL; em Railway, se não vier, usa localhost:3001.
        """
        railway_environment = os.getenv("RAILWAY_ENVIRONMENT_NAME")
        explicit_url = os.getenv("WHATSAPP_SERVICE_URL")

        if explicit_url:
            self.baileys_url = explicit_url.rstrip("/")
        elif railway_environment:
            self.baileys_url = "http://127.0.0.1:3001"
        else:
            self.baileys_url = "http://localhost:3001"

        self.api_token: Optional[str] = os.getenv("WHATSAPP_API_TOKEN") or None

        # timeouts “amigáveis” ao Railway
        self.short_timeout = int(os.getenv("WHATSAPP_HTTP_TIMEOUT_SHORT", "20"))
        self.long_timeout = int(os.getenv("WHATSAPP_HTTP_TIMEOUT_LONG", "45"))

        self.headers = {"Content-Type": "application/json"}
        if self.api_token:
            self.headers["Authorization"] = f"Bearer {self.api_token}"

        logger.info(f"WhatsApp Service initialized with URL: {self.baileys_url}")

    # ------------- helpers HTTP -------------

    def _http(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
        allow_404: bool = False,
    ) -> Dict[str, Any]:
        """
        Pequeno wrapper HTTP com log e tratamento de erros.
        """
        url = f"{self.baileys_url}{path}"
        to = timeout or self.short_timeout

        try:
            resp = requests.request(
                method=method.upper(),
                url=url,
                headers=self.headers,
                json=json_body,
                timeout=to,
            )
            if allow_404 and resp.status_code == 404:
                return {"success": False, "error": "Not found", "status": 404}

            if resp.status_code >= 400:
                return {
                    "success": False,
                    "error": f"HTTP Error: {resp.status_code}",
                    "details": resp.text,
                }

            # Tenta parsear JSON, caindo para texto se necessário
            try:
                data = resp.json()
            except ValueError:
                data = {"raw": resp.text}

            # Normaliza uma flag de sucesso
            if "success" not in data:
                data["success"] = True

            return data

        except requests.exceptions.Timeout:
            return {"success": False, "error": "Timeout", "details": f"timeout={to}s"}
        except requests.exceptions.ConnectionError as e:
            return {
                "success": False,
                "error": "Connection error",
                "details": str(e),
            }
        except Exception as e:
            return {"success": False, "error": "Unexpected error", "details": str(e)}

    # ------------- API principal -------------

    def send_message(self, phone_number: str, message: str, user_id: int) -> Dict[str, Any]:
        """
        Envia mensagem via /send/:sessionId. Formata número para +55 se necessário.
        """
        try:
            clean = "".join(filter(str.isdigit, phone_number or ""))
            if not clean:
                return {"success": False, "error": "Invalid phone"}

            if not clean.startswith("55"):
                clean = "55" + clean

            payload = {"number": clean, "message": message}

            res = self._http("POST", f"/send/{user_id}", json_body=payload, timeout=self.long_timeout)
            if res.get("success"):
                logger.info(f"WhatsApp message sent to {clean}")
                return {
                    "success": True,
                    "message_id": res.get("messageId") or res.get("id"),
                    "response": res,
                }

            # Se falhou por não conectado, tenta restaurar e informa
            err_txt = (res.get("error") or "").lower()
            if "not connected" in err_txt or "não conectado" in err_txt:
                self.restore_session(user_id)

            return res

        except Exception as e:
            logger.error(f"Unexpected error sending WhatsApp message: {e}")
            return {"success": False, "error": "Unexpected error", "details": str(e)}

    def get_health_status(self) -> Dict[str, Any]:
        """GET /health"""
        return self._http("GET", "/health")

    def check_instance_status(self, user_id: int) -> Dict[str, Any]:
        """GET /status/:sessionId"""
        res = self._http("GET", f"/status/{user_id}")
        # Normaliza campos úteis
        if res.get("success", False):
            return {
                "success": True,
                "connected": bool(res.get("connected")),
                "state": res.get("state", "unknown"),
                "qrCode": res.get("qrCode"),
                "response": res,
            }
        return res

    def restore_session(self, user_id: int) -> Dict[str, Any]:
        """
        Restaura sessão pedindo /reconnect/:sessionId.
        """
        return self._http("POST", f"/reconnect/{user_id}", timeout=self.long_timeout)

    def disconnect_whatsapp(self, user_id: int) -> Dict[str, Any]:
        """POST /disconnect/:sessionId"""
        return self._http("POST", f"/disconnect/{user_id}")

    def reconnect_whatsapp(self, user_id: int) -> Dict[str, Any]:
        """POST /reconnect/:sessionId"""
        return self._http("POST", f"/reconnect/{user_id}", timeout=self.long_timeout)

    def request_pairing_code(self, user_id: int, phone_number: str) -> Dict[str, Any]:
        """
        POST /pairing-code/:sessionId  { phoneNumber }
        (se o seu servidor não implementa, retorne erro amigável)
        """
        payload = {"phoneNumber": phone_number}
        res = self._http(
            "POST",
            f"/pairing-code/{user_id}",
            json_body=payload,
            timeout=self.long_timeout,
            allow_404=True,
        )
        if res.get("status") == 404:
            return {
                "success": False,
                "error": "pairing-code endpoint not available on server",
            }
        return {
            "success": bool(res.get("success")),
            "pairing_code": res.get("pairingCode") or res.get("code"),
            "response": res,
        }

    def get_pairing_code(self, user_id: int) -> Dict[str, Any]:
        """GET /pairing-code/:sessionId (se existir)"""
        res = self._http("GET", f"/pairing-code/{user_id}", allow_404=True)
        if res.get("status") == 404:
            return {
                "success": False,
                "error": "pairing-code endpoint not available on server",
            }
        return res

    def get_qr_code(self, user_id: int) -> Dict[str, Any]:
        """
        Usa /status/:id e extrai qrCode (se houver).
        """
        status = self.check_instance_status(user_id)
        if status.get("success") and status.get("qrCode"):
            return {
                "success": True,
                "qrCode": status["qrCode"],
                "connected": status.get("connected", False),
                "state": status.get("state", "unknown"),
            }
        return {
            "success": False,
            "error": "QR Code not available",
            "details": status,
        }

    def force_new_qr(self, user_id: int) -> Dict[str, Any]:
        """
        Estratégia robusta (sem /force-qr):
        1) Tenta desconectar (best-effort).
        2) Reconecta (/reconnect/:id) para forçar emissão de QR.
        3) Faz polling de /status/:id por até N segundos esperando `qrCode`.
        """
        # 1) tenta desconectar (ignora erro)
        try:
            self.disconnect_whatsapp(user_id)
        except Exception:
            pass

        # 2) reconectar
        rec = self.reconnect_whatsapp(user_id)
        if not rec.get("success"):
            return {
                "success": False,
                "error": "Reconnect failed",
                "details": rec,
            }

        # 3) polling do status
        max_wait = int(os.getenv("WHATSAPP_FORCE_QR_TIMEOUT", "30"))
        interval = 2
        waited = 0

        while waited < max_wait:
            status = self.check_instance_status(user_id)
            if status.get("success") and status.get("qrCode"):
                return {
                    "success": True,
                    "qrCode": status["qrCode"],
                    "state": status.get("state"),
                    "connected": status.get("connected"),
                }
            time.sleep(interval)
            waited += interval

        return {
            "success": False,
            "error": "QR Code not available after reconnect/polling",
        }

    def format_message(self, template: str, **kwargs) -> str:
        """Formatador simples de templates .format(**kwargs)"""
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
